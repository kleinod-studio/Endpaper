// After a book has been replaced by a newer edition, marks and bookmarks must still find their words.
// A place is kept as a CFI, whose first step is the chapter's position in the reading order; a new edition
// may add chapters before it, or change words in it. So each place also keeps the file of its chapter and its
// quote. The chapter is found again by its file, and words that moved are found again by the quote.
// Only the places that changed are reported; what cannot be found stays in the notes file as it is.
import * as CFI from './foliate-js/epubcfi.js'
import { quoteOf, rangeIn, wordsOf } from './marks.js'
import { blockOf, firstWordIn } from './bookmarks.js'

const MAX_QUOTE = 160 // as bookmarks.js cuts the beginning of a paragraph

// The chapters read here are not laid out: a paragraph is known by its element, not by how it shows.
const BLOCKS = new Set(['p', 'div', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'section', 'article',
    'aside', 'header', 'footer', 'nav', 'main', 'td', 'th', 'caption', 'dd', 'dt', 'figure', 'figcaption', 'pre',
    'address', 'table', 'ul', 'ol', 'dl', 'body'])

export const blockIn = node => {
    let element = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement
    if (element.ownerDocument.defaultView) return blockOf(element)
    while (element.parentElement && !BLOCKS.has(element.localName)) element = element.parentElement
    return element
}

export const firstWordOf = block => {
    if (block.ownerDocument.defaultView) return firstWordIn(block)
    const walker = block.ownerDocument.createTreeWalker(block, NodeFilter.SHOW_TEXT)
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        if (node.parentElement?.closest('script, style, [hidden]')) continue
        const words = wordsOf(node)
        if (words.length) return rangeIn(node, words[0])
    }
    return null
}

export const tidy = text => (text ?? '').replace(/­/g, '').replace(/\s+/g, ' ').trim()

// The text of a chapter, with whitespace taken together as quoteOf does, and for every letter where it stands.
const textMap = doc => {
    const letters = [], nodes = [], offsets = []
    const walker = doc.createTreeWalker(doc.body ?? doc.documentElement, NodeFilter.SHOW_TEXT)
    let space = true
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const value = node.nodeValue
        for (let i = 0; i < value.length; i++) {
            let letter = value[i]
            if (letter === '­') continue
            if (/\s/.test(letter)) {
                if (space) continue
                letter = ' '
                space = true
            } else space = false
            letters.push(letter)
            nodes.push(node)
            offsets.push(i)
        }
    }
    return { text: letters.join(''), nodes, offsets }
}

// The words in the chapter; of several places with the same words, the one nearest to where they were.
export const findText = (doc, wanted, near) => {
    const map = textMap(doc)
    let best = -1
    for (let at = map.text.indexOf(wanted); at >= 0; at = map.text.indexOf(wanted, at + 1)) {
        if (best < 0 || near == null || Math.abs(at - near) < Math.abs(best - near)) best = at
        if (near == null) break
    }
    if (best < 0) return null
    const last = best + wanted.length - 1
    const range = doc.createRange()
    range.setStart(map.nodes[best], map.offsets[best])
    range.setEnd(map.nodes[last], map.offsets[last] + 1)
    return range
}

// Where a range starts, counted in the letters of textMap: to choose among places with the same words.
const letterAt = (doc, range) => {
    const map = textMap(doc)
    for (let i = 0; i < map.nodes.length; i++) {
        const order = range.comparePoint(map.nodes[i], map.offsets[i])
        if (order >= 0) return i
    }
    return null
}

// The text a bookmark was set on: its paragraph from the first word, cut as bookmarks.js cuts it.
export const bookmarkText = word => {
    const block = blockIn(word.startContainer)
    const range = word.cloneRange()
    range.setEnd(block, block.childNodes.length)
    return tidy(quoteOf(range))
}

export const createPlaces = view => {
    const inChapter = (cfi, index) => {
        const inner = cfi.match(/^epubcfi\([^!]*!(.*)\)$/)?.[1]
        return inner ? CFI.joinIndir(view.getCFI(index), inner) : null
    }

    return {
        // items: [{ kind: 'mark' | 'bookmark', id, cfi, file, quote }]
        async check(items) {
            const { sections } = view.book
            const documents = new Map()
            const documentOf = async index => {
                if (!documents.has(index)) documents.set(index, await sections[index].createDocument())
                return documents.get(index)
            }
            const results = []
            for (const item of items) {
                try {
                    const result = await this.checkOne(item, sections, documentOf)
                    if (result) results.push(result)
                } catch (error) {
                    console.warn('Place not checked', item.cfi, error)
                }
            }
            return results
        },

        async checkOne(item, sections, documentOf) {
            const bookmark = item.kind === 'bookmark'
            let cfi = item.cfi
            let index = null
            try {
                index = view.resolveCFI(cfi).index
            } catch {
                // a place that no longer fits the book: found by its file or its words below
            }
            const byFile = item.file ? sections.findIndex(section => section.id === item.file) : -1
            if (byFile >= 0 && byFile !== index) {
                cfi = inChapter(cfi, byFile) ?? cfi
                index = byFile
            }
            const valid = Number.isInteger(index) && index >= 0 && index < sections.length
            let wanted = tidy(item.quote)
            if (bookmark && wanted.endsWith('…')) wanted = wanted.slice(0, -1).trim()
            const fits = range => {
                if (!range || !wanted) return Boolean(range)
                const text = bookmark ? bookmarkText(range) : tidy(quoteOf(range))
                if (!bookmark) return text === wanted
                return wanted.length >= MAX_QUOTE ? text.startsWith(wanted) : text === wanted || text.startsWith(wanted)
            }
            const placeIn = async i => {
                const doc = await documentOf(i)
                let range = null
                try {
                    range = view.resolveCFI(i === index ? cfi : inChapter(cfi, i)).anchor(doc)
                } catch {
                    range = null
                }
                return { doc, range }
            }
            const answer = (i, newCfi) => {
                const file = sections[i]?.id ?? null
                if (newCfi === item.cfi && file === item.file) return null
                return { kind: item.kind, id: item.id, cfi: newCfi, file }
            }
            if (valid) {
                const { range } = await placeIn(index)
                if (fits(range)) return answer(index, cfi)
            }
            if (!wanted) return valid ? answer(index, cfi) : { kind: item.kind, id: item.id, lost: true }
            // The words moved: looked for in their own chapter first, then in the others.
            const order = valid ? [index, ...sections.keys()].filter((i, n, all) => all.indexOf(i) === n) : [...sections.keys()]
            for (const i of order) {
                if (sections[i].linear === 'no' && i !== index) continue
                const { doc, range: old } = await placeIn(i)
                const near = old && i === index ? letterAt(doc, old) : null
                const found = findText(doc, wanted, near)
                if (!found) continue
                const place = bookmark ? firstWordOf(blockIn(found.startContainer)) : found
                if (place) return answer(i, view.getCFI(i, place))
            }
            return { kind: item.kind, id: item.id, lost: true }
        },
    }
}
