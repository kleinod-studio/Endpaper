// Searching the open book. foliate-js finds the places and outlines them; what the running text hides, such as
// footnotes shown in the popup, is left out, because a jump there would lead to nothing visible.
// Each result comes with an excerpt that shows the reader's own marks in it, and the notes and answers that
// reader_view.py found are put in between, in the order of the book.
import * as CFI from './foliate-js/epubcfi.js'
import { Overlayer } from './foliate-js/overlayer.js'
import { HIDDEN, hideNotes } from './peek.js'

const CURRENT = 'endpaper-search-current'
const COLOR = '#3584e4'
const PROGRESS_STEP = 0.05
const CONTEXT = 50 // characters on either side of the word

// Where a range covers the characters start..end of a text node, as offsets, or null.
const overlap = (node, start, end, range) => {
    let low
    if (range.startContainer === node) low = Math.max(start, range.startOffset)
    else if (range.comparePoint(node, start) === 0) low = start
    else return null
    const high = range.endContainer === node ? Math.min(end, range.endOffset) : end
    return low < high ? [low, high] : null
}

// The characters of a text node from start to end, each knowing whether it is the word and how it is marked.
const charsOf = (node, start, end, marks, word) => {
    const chars = [...node.nodeValue.slice(start, end)].map(text => ({ text, word, color: null, underline: false }))
    for (const mark of marks) {
        const found = overlap(node, start, end, mark.range)
        if (!found) continue
        // Code units and characters differ only outside the basic plane, which is rare enough in books.
        for (let i = found[0] - start; i < found[1] - start && i < chars.length; i++) {
            chars[i].color = mark.hex
            chars[i].underline = mark.style === 'underline'
        }
    }
    return chars
}

const tidy = chars => {
    const out = []
    for (const char of chars) {
        if (char.text === '­') continue // a soft hyphen
        if (/\s/.test(char.text)) {
            if (out.at(-1)?.text === ' ') continue
            out.push({ ...char, text: ' ' })
        } else out.push(char)
    }
    return out
}

const plain = text => [...text].map(t => ({ text: t, word: false, color: null, underline: false }))

// Neighbouring characters that look the same become one part.
const partsOf = chars => {
    const parts = []
    for (const { text, word, color, underline } of chars) {
        const last = parts.at(-1)
        if (last && last.word === word && last.color === color && last.underline === underline) last.text += text
        else parts.push({ text, word, color, underline })
    }
    return parts
}

// The text nodes of the paragraph around a result, walked from the result: an excerpt may reach over emphasis
// and links, but not into the next paragraph.
const BLOCKS = 'p,li,dd,dt,blockquote,td,th,figcaption,h1,h2,h3,h4,h5,h6,div,section,article,aside,body'
const walkerAround = node => {
    const block = node.parentElement?.closest(BLOCKS) ?? node.ownerDocument.body
    const walker = node.ownerDocument.createTreeWalker(block, NodeFilter.SHOW_TEXT, {
        acceptNode: text => text.parentElement?.closest(`script,style,[${HIDDEN}]`)
            ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    })
    walker.currentNode = node
    return walker
}

// The excerpt around a result, cut at whole words.
export const excerptOf = (range, marks) => {
    const { startContainer: first, startOffset, endContainer: last, endOffset } = range
    if (first.nodeType !== Node.TEXT_NODE || last.nodeType !== Node.TEXT_NODE) return null
    let walker = walkerAround(first)
    let before = charsOf(first, 0, startOffset, marks, false)
    for (let node = walker.previousNode(); node && before.length <= CONTEXT; node = walker.previousNode())
        before = [...charsOf(node, 0, node.nodeValue.length, marks, false), ...before]
    const cutBefore = before.length > CONTEXT
    before = tidy(before.slice(-CONTEXT))
    walker = walkerAround(last)
    let after = charsOf(last, endOffset, last.nodeValue.length, marks, false)
    for (let node = walker.nextNode(); node && after.length <= CONTEXT; node = walker.nextNode())
        after = [...after, ...charsOf(node, 0, node.nodeValue.length, marks, false)]
    const cutAfter = after.length > CONTEXT
    after = tidy(after.slice(0, CONTEXT))
    const word = first === last
        ? charsOf(first, startOffset, endOffset, marks, true)
        : [...charsOf(first, startOffset, first.nodeValue.length, marks, true), ...charsOf(last, 0, endOffset, marks, true)]
    while (before[0]?.text === ' ') before.shift()
    while (after.at(-1)?.text === ' ') after.pop()
    if (cutBefore) {
        const space = before.findIndex(char => char.text === ' ')
        if (space >= 0 && space < before.length - 1) before = before.slice(space + 1)
        before = [...plain('… '), ...before]
    }
    if (cutAfter) {
        const space = after.findLastIndex(char => char.text === ' ')
        if (space > 0) after = after.slice(0, space)
        after = [...after, ...plain(' …')]
    }
    return partsOf([...before, ...tidy(word), ...after])
}

// The element the library search found a passage in, by its places among the element children.
const elementAt = (doc, path) => {
    let element = doc?.documentElement
    for (const place of path ?? []) element = element?.children[place]
    return element ?? null
}

// Whether a range starts in the element or after it.
const atOrAfter = (range, element) => element.contains(range.startContainer)
    || !!(element.compareDocumentPosition(range.startContainer) & Node.DOCUMENT_POSITION_FOLLOWING)

export const createSearch = (view, post, { marks }) => {
    let run = 0 // the search going on, numbered by reader_view.py; another number makes it stop
    let queue = Promise.resolve()
    let current = null
    const prepared = new WeakSet()
    let searched = null // the document being searched, a fresh copy of its section

    const acceptNode = node => {
        if (node.nodeType !== Node.ELEMENT_NODE) return NodeFilter.FILTER_ACCEPT
        const doc = node.ownerDocument
        searched = doc
        if (!prepared.has(doc)) {
            prepared.add(doc)
            hideNotes(doc) // the documents searched are fresh copies, not the ones on screen
        }
        const name = node.localName
        if (name === 'script' || name === 'style' || node.hasAttribute(HIDDEN)) return NodeFilter.FILTER_REJECT
        return NodeFilter.FILTER_SKIP
    }

    const clearCurrent = () => {
        current?.overlayer?.remove(CURRENT)
        current = null
    }

    const indexOf = cfi => {
        try {
            return view.resolveNavigation(cfi)?.index ?? -1
        } catch {
            return -1
        }
    }

    // The reader's marks in a section, as ranges in the document being searched.
    const marksIn = (index, doc, bySection) => (bySection.get(index) ?? []).flatMap(mark => {
        try {
            const range = view.resolveNavigation(mark.cfi).anchor(doc)
            return range ? [{ ...mark, range }] : []
        } catch {
            return []
        }
    })

    const search = async (query, mine, extras, near) => {
        if (mine !== run) return
        clearCurrent()
        view.clearSearch()
        if (!query) return
        const sections = view.book.sections.length
        const marksBySection = new Map()
        for (const mark of marks()) {
            const index = indexOf(mark.cfi)
            marksBySection.set(index, [...(marksBySection.get(index) ?? []), mark])
        }
        // Notes by the place of their mark, answers at the end of their chapter; what has no place, at the end.
        const placeOf = item => {
            const index = item.kind === 'answer'
                ? view.book.sections.findIndex(section => section.id === item.field.slice(0, item.field.indexOf('#')))
                : indexOf(item.cfi)
            return index < 0 ? sections : index
        }
        const waiting = extras.map(item => ({ ...item, index: placeOf(item) })).sort((a, b) => a.index - b.index
            || (a.kind === 'answer') - (b.kind === 'answer')
            || (a.kind === 'note' && b.kind === 'note' ? CFI.compare(a.cfi, b.cfi) : 0))
        let count = 0
        const send = (chapter, items) => {
            if (!items.length) return
            count += items.length
            post('search-results', { run: mine, chapter, items })
        }
        // What was found by reader_view.py in chapters the search has passed, each under its own chapter.
        const sendWaiting = before => {
            while (waiting.length && waiting[0].index < before) {
                const index = waiting[0].index
                const group = []
                while (waiting.length && waiting[0].index === index) group.push(waiting.shift())
                send(group[0].chapter ?? '', group)
            }
        }
        let shown = 0
        try {
            const results = view.search({
                query, acceptNode,
                draw: Overlayer.outline, drawOptions: { color: COLOR, width: 2, radius: 3, padding: 1 },
            })
            for await (const result of results) {
                if (mine !== run) return
                if (result === 'done') break
                if (result.subitems) {
                    const index = indexOf(result.subitems[0].cfi)
                    sendWaiting(index)
                    const doc = searched
                    const inSection = doc ? marksIn(index, doc, marksBySection) : []
                    // The result the library search led here: the first one in its passage, or after it.
                    const target = near?.section === index ? elementAt(doc, near.path) : null
                    const found = result.subitems.map(({ cfi, excerpt }) => {
                        let range = null
                        try {
                            range = doc && view.resolveNavigation(cfi).anchor(doc)
                        } catch {
                            range = null
                        }
                        const parts = (range && excerptOf(range, inSection.filter(m => m.range))) ?? [
                            { text: excerpt.pre }, { text: excerpt.match, word: true }, { text: excerpt.post },
                        ]
                        const item = { kind: 'text', cfi, parts }
                        if (target && near && range && atOrAfter(range, target)) {
                            item.near = true
                            near = null
                        }
                        return item
                    })
                    const items = []
                    for (const item of found) {
                        while (waiting[0]?.index === index && waiting[0].kind === 'note'
                            && CFI.compare(waiting[0].cfi, item.cfi) < 0) items.push(waiting.shift())
                        items.push(item)
                    }
                    while (waiting[0]?.index === index) items.push(waiting.shift())
                    send(result.label.trim(), items)
                } else if (result.progress != null) {
                    // A chapter without results can still have notes; they come once it is passed.
                    sendWaiting(Math.round(result.progress * sections) - 1)
                    if (result.progress >= shown + PROGRESS_STEP) {
                        shown = result.progress
                        post('search-progress', { run: mine, progress: result.progress })
                    }
                }
            }
            sendWaiting(Infinity)
            post('search-done', { run: mine, count })
        } catch (error) {
            sendWaiting(Infinity)
            post('search-done', { run: mine, count, failed: String(error) })
        }
    }

    return {
        start(query, mine, extras = [], near = null) {
            run = mine
            queue = queue.then(() => search(query, mine, extras, near))
            return queue
        },
        async goToPassage(section, path) {
            await view.goTo(section)
            const doc = view.renderer.getContents().find(content => content.index === section)?.doc
            const element = elementAt(doc, path)
            if (element) await view.renderer.scrollToAnchor(element)
        },
        stop() {
            run = 0
            queue = queue.then(() => {
                clearCurrent()
                view.clearSearch()
            })
        },
        async goTo(cfi) {
            await view.goTo(cfi)
            clearCurrent()
            const { index, anchor } = view.resolveNavigation(cfi)
            const content = view.renderer.getContents().find(item => item.index === index)
            if (!content?.overlayer) return
            content.overlayer.add(CURRENT, anchor(content.doc), Overlayer.highlight, { color: COLOR })
            current = content
        },
    }
}
