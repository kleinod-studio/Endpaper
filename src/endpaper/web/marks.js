// Marks in the text, made without dragging: in marking mode one click on the first word and one on
// the last mark everything between them, also across pages. Two clicks on one word mark that word.
import { Overlayer } from './foliate-js/overlayer.js'

const PENDING = 'endpaper-pending'
const CURSOR = 'endpaper-cursor'
const PREVIEW = 'endpaper-preview'
const POINT_TOLERANCE = 2
const CLOSING = /^[\p{Pe}\p{Pf}\p{Po}]+/u
const OPENING = /[\p{Ps}\p{Pi}]+$/u

const hits = (range, x, y) => [...range.getClientRects()].some(rect =>
    x >= rect.left - POINT_TOLERANCE && x <= rect.right + POINT_TOLERANCE
    && y >= rect.top - POINT_TOLERANCE && y <= rect.bottom + POINT_TOLERANCE)

// The word under the pointer, or null in the margin or on a picture. On punctuation, the word before it.
export const wordAt = (doc, x, y) => {
    const caret = doc.caretRangeFromPoint(x, y)
    const node = caret?.startContainer
    if (node?.nodeType !== Node.TEXT_NODE) return null
    const segmenter = new Intl.Segmenter(doc.documentElement.lang || undefined, { granularity: 'word' })
    const segments = [...segmenter.segment(node.data)]
    const rangeOf = ({ segment, index }) => {
        const range = doc.createRange()
        range.setStart(node, index)
        range.setEnd(node, index + segment.length)
        return range
    }
    const found = segments.findIndex(s => s.segment.trim() && hits(rangeOf(s), x, y))
    if (found < 0) return null
    for (let i = found; i >= 0; i--) if (segments[i].isWordLike) return rangeOf(segments[i])
    return null
}

// --- Going from word to word with the keyboard -------------------------------------------------

const wordsIn = new WeakMap()

export const wordsOf = node => {
    if (!wordsIn.has(node)) {
        const segmenter = new Intl.Segmenter(node.ownerDocument.documentElement.lang || undefined, { granularity: 'word' })
        wordsIn.set(node, [...segmenter.segment(node.data)].filter(s => s.isWordLike)
            .map(({ index, segment }) => [index, index + segment.length]))
    }
    return wordsIn.get(node)
}

export const shown = node => {
    const parent = node.parentElement
    if (!parent?.getClientRects().length) return false
    const folded = parent.closest('details:not([open])')
    return !folded || Boolean(parent.closest('summary'))
}

const walkerAt = (doc, node) => {
    const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT)
    if (node) walker.currentNode = node
    return walker
}

export const rangeIn = (node, [start, end]) => {
    const range = node.ownerDocument.createRange()
    range.setStart(node, start)
    range.setEnd(node, end)
    return range
}

// The word after or before this one, also in other elements. Null at the end of the section.
const neighbor = (word, forward) => {
    const node = word.startContainer
    const inNode = wordsOf(node)
    const here = inNode.findIndex(([start]) => start === word.startOffset)
    const next = inNode[here + (forward ? 1 : -1)]
    if (here >= 0 && next) return rangeIn(node, next)
    const walker = walkerAt(node.ownerDocument, node)
    while (forward ? walker.nextNode() : walker.previousNode()) {
        const words = shown(walker.currentNode) ? wordsOf(walker.currentNode) : []
        if (words.length) return rangeIn(walker.currentNode, forward ? words[0] : words[words.length - 1])
    }
    return null
}

export const firstRect = range => range.getClientRects()[0]

export const onScreen = range => {
    const rect = firstRect(range)
    const frame = range.startContainer.ownerDocument.defaultView.frameElement?.getBoundingClientRect()
    if (!rect || !frame) return false
    const x = frame.left + rect.left + rect.width / 2
    const y = frame.top + rect.top + rect.height / 2
    return x >= 0 && x <= window.innerWidth && y >= 0 && y <= window.innerHeight
}

export const firstOnScreen = doc => {
    const walker = walkerAt(doc)
    while (walker.nextNode()) {
        if (!shown(walker.currentNode)) continue
        for (const words of wordsOf(walker.currentNode)) {
            const range = rangeIn(walker.currentNode, words)
            if (onScreen(range)) return range
        }
    }
    return null
}

// The word in the next or previous line that stands closest above or below this one.
const wordInLine = (word, forward) => {
    const start = firstRect(word)
    if (!start) return null
    const center = start.left + start.width / 2
    const beyond = rect => (forward
        ? rect.top >= start.bottom - 1 || rect.left > start.right + start.width * 4 + 400
        : rect.bottom <= start.top + 1 || rect.right < start.left - start.width * 4 - 400)
    let current = neighbor(word, forward)
    while (current && !beyond(firstRect(current) ?? start)) current = neighbor(current, forward)
    if (!current) return null
    const line = firstRect(current)
    for (;;) {
        const next = neighbor(current, forward)
        const rect = next && firstRect(next)
        if (!rect || Math.abs(rect.top - line.top) > line.height / 2) return current
        const here = firstRect(current)
        const distance = r => Math.abs(r.left + r.width / 2 - center)
        if (distance(rect) >= distance(here)) return current
        current = next
    }
}

const span = (first, last) => {
    const range = first.cloneRange()
    range.setEnd(last.endContainer, last.endOffset)
    const closing = CLOSING.exec(last.endContainer.data.slice(last.endOffset))
    if (closing) range.setEnd(last.endContainer, last.endOffset + closing[0].length)
    const opening = OPENING.exec(first.startContainer.data.slice(0, first.startOffset))
    if (opening) range.setStart(first.startContainer, first.startOffset - opening[0].length)
    return range
}

const SVG = 'http://www.w3.org/2000/svg'
const NOTE_LINE_SHARE = 0.7 // the sign's size, as a share of the height of the line
const NOTE_MIN_SIZE = 10
const NOTE_MAX_SIZE = 18
const NOTE_GAP = 12
const NOTE_MIN_GAP = 4
const NOTE_EDGE = 2 // in a narrow window the sign moves closer to the text, but stays on the page
const NOTE_REACH = 3 // a little more room around it for the click

export const svgElement = (tag, attributes) => {
    const element = document.createElementNS(SVG, tag)
    for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value)
    return element
}

// Where the note sign goes: in the margin beside the first line of the mark, wherever in the line the mark starts.
const notePlace = (range, first) => {
    let block = range.startContainer
    if (block.nodeType !== Node.ELEMENT_NODE) block = block.parentElement
    const view = block.ownerDocument.defaultView
    while (block.parentElement && view.getComputedStyle(block).display === 'inline') block = block.parentElement
    const line = first.top + first.height / 2
    const box = [...block.getClientRects()].find(rect => line >= rect.top && line <= rect.bottom) ?? first
    const wanted = Math.min(NOTE_MAX_SIZE, Math.max(NOTE_MIN_SIZE, Math.round(first.height * NOTE_LINE_SHARE)))
    if (view.getComputedStyle(block).direction === 'rtl') {
        return { x: box.right + NOTE_GAP, y: first.top + (first.height - wanted) / 2, size: wanted }
    }
    // Where the margin is narrow, the sign keeps clear of the text first of all.
    const room = box.left - NOTE_EDGE
    const size = Math.max(NOTE_MIN_SIZE, Math.min(wanted, room - NOTE_MIN_GAP))
    const y = first.top + (first.height - size) / 2
    return { x: Math.max(NOTE_EDGE, box.left - Math.min(NOTE_GAP, Math.max(NOTE_MIN_GAP, room - size)) - size), y, size }
}

const noteSign = ({ x, y, size }, color) => svgElement('rect', {
    class: 'endpaper-note', x, y, width: size, height: size, rx: 2, fill: color,
})

export const quoteOf = range => range.toString().replace(/­/g, '').replace(/\s+/g, ' ').trim()

export const createMarks = (view, post) => {
    const marks = new Map() // by CFI: { cfi, hex, style, hasNote }
    const signs = new Map() // by CFI: where the note sign of a mark is drawn
    const sections = new Map() // section index → the CFIs in it
    let marking = false
    let hex = '#f5c211'
    let pending = null
    let cursor = null
    let keys = Promise.resolve()
    let lastClick = { x: 0, y: 0 }

    const contentOf = doc => view.renderer.getContents().find(c => c.doc === doc)

    const drawCursor = () => {
        if (pending) {
            const overlayer = contentOf(pending.doc)?.overlayer
            overlayer?.remove(PREVIEW)
            if (cursor && cursor.startContainer.ownerDocument === pending.doc && overlayer) {
                const order = pending.word.compareBoundaryPoints(Range.START_TO_START, cursor)
                const range = order <= 0 ? span(pending.word, cursor) : span(cursor, pending.word)
                overlayer.add(PREVIEW, range, Overlayer.highlight, { color: hex })
            }
        }
        for (const { overlayer } of view.renderer.getContents()) overlayer?.remove(CURSOR)
        if (!cursor) return
        const doc = cursor.startContainer.ownerDocument
        const color = doc.defaultView.getComputedStyle(cursor.startContainer.parentElement).color
        contentOf(doc)?.overlayer?.add(CURSOR, cursor, Overlayer.outline, { color, width: 2, radius: 3, padding: 1 })
    }

    const clearCursor = () => {
        cursor = null
        drawCursor()
    }

    const clearPending = () => {
        if (!pending) return
        const overlayer = contentOf(pending.doc)?.overlayer
        overlayer?.remove(PENDING)
        overlayer?.remove(PREVIEW)
        pending = null
        post('mark-pending', { active: false })
    }

    const startAt = (doc, word) => {
        clearPending()
        pending = { doc, word }
        contentOf(doc)?.overlayer?.add(PENDING, word, Overlayer.outline, { color: hex, width: 2 })
        post('mark-pending', { active: true })
    }

    const finish = (doc, range) => {
        const content = contentOf(doc)
        clearPending()
        post('mark-range', {
            cfi: view.getCFI(content.index, range),
            quote: quoteOf(range),
            chapter: view.getProgressOf(content.index, range)?.tocItem?.label?.trim() ?? null,
        })
    }

    const choose = (doc, word) => {
        if (!pending) return startAt(doc, word)
        if (pending.doc !== doc) {
            post('mark-other-chapter')
            return startAt(doc, word)
        }
        const order = pending.word.compareBoundaryPoints(Range.START_TO_START, word)
        if (order === 0) return finish(doc, word)
        finish(doc, order < 0 ? span(pending.word, word) : span(word, pending.word))
    }

    const onClick = (doc, x, y) => {
        const word = wordAt(doc, x, y)
        if (!word) return
        if (cursor) cursor = word
        choose(doc, word)
        drawCursor()
    }

    const currentDoc = () => view.renderer.getContents()[0]?.doc

    const placeCursor = async (word, forward) => {
        cursor = word
        // The next page comes along by itself when the cursor leaves this one.
        for (let tries = 0; tries < 3 && word && !onScreen(word); tries++) await (forward ? view.next() : view.prev())
        drawCursor()
    }

    // At the end of a section the next one follows. A mark cannot go along, so the cursor starts there anew.
    const moveTo = async (next, forward) => {
        if (next) return placeCursor(next, forward)
        await (forward ? view.next() : view.prev())
        cursor = currentDoc() ? firstOnScreen(currentDoc()) : null
        drawCursor()
    }

    const validCursor = () => {
        if (cursor && !contentOf(cursor.startContainer.ownerDocument)) cursor = null
        return cursor
    }

    const markUnderCursor = () => {
        const content = contentOf(cursor.startContainer.ownerDocument)
        const rect = firstRect(cursor)
        if (!content?.overlayer || !rect) return null
        for (const key of [CURSOR, PENDING, PREVIEW]) content.overlayer.remove(key)
        const [value] = content.overlayer.hitTest({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 })
        if (pending) content.overlayer.add(PENDING, pending.word, Overlayer.outline, { color: hex, width: 2 })
        drawCursor()
        return value && marks.has(value) ? { value, doc: content.doc, rect } : null
    }

    const onKey = async name => {
        if (!marking) return
        if (name === 'page-next' || name === 'page-previous') {
            await (name === 'page-next' ? view.next() : view.prev())
            if (validCursor() || currentDoc()) cursor = currentDoc() ? firstOnScreen(currentDoc()) : null
            return drawCursor()
        }
        if (!validCursor()) {
            cursor = pending && contentOf(pending.doc) ? pending.word : currentDoc() && firstOnScreen(currentDoc())
            if (!cursor) return
            if (name === 'left' || name === 'right' || name === 'up' || name === 'down') return drawCursor()
        }
        if (name === 'right' || name === 'left') return moveTo(neighbor(cursor, name === 'right'), name === 'right')
        if (name === 'down' || name === 'up') return moveTo(wordInLine(cursor, name === 'down'), name === 'down')
        if (name === 'choose') {
            choose(cursor.startContainer.ownerDocument, cursor)
            return drawCursor()
        }
        if (name === 'menu') {
            const mark = markUnderCursor()
            if (!mark) return post('mark-none')
            openMark(mark.value, mark.doc, mark.rect.left + mark.rect.width / 2, mark.rect.top, false, true)
        }
    }

    const draw = async mark => {
        const { index } = await view.addAnnotation({ value: mark.cfi })
        if (!sections.has(index)) sections.set(index, new Set())
        sections.get(index).add(mark.cfi)
    }

    // A mark that no longer fits the book must not get in the way of reading. It stays in the notes file.
    const drawSafely = mark => draw(mark).catch(error => console.warn('Mark not shown', mark.cfi, error))

    // The drawing lets clicks through, so that the text stays clickable: the sign is found by its place.
    const signAt = (doc, x, y) => {
        for (const [cfi, sign] of signs) {
            if (sign.doc === doc && x >= sign.x - NOTE_REACH && x <= sign.x + sign.size + NOTE_REACH
                && y >= sign.y - NOTE_REACH && y <= sign.y + sign.size + NOTE_REACH) return cfi
        }
        return null
    }

    const openMark = (cfi, doc, x, y, sign = false, menu = false) => {
        const frame = doc.defaultView.frameElement?.getBoundingClientRect()
        post('mark-clicked', { cfi, sign, menu, x: Math.round(x + (frame?.left ?? 0)), y: Math.round(y + (frame?.top ?? 0)) })
    }

    view.addEventListener('draw-annotation', ({ detail: { draw: paint, annotation, doc, range } }) => {
        const mark = marks.get(annotation.value)
        if (!mark) return
        const base = mark.style === 'underline' ? Overlayer.underline : Overlayer.highlight
        paint((rects, options) => {
            const group = svgElement('g', {})
            group.append(base(rects, options))
            signs.delete(mark.cfi)
            if (mark.hasNote && rects.length) {
                const place = notePlace(range, rects[0])
                group.append(noteSign(place, mark.hex))
                signs.set(mark.cfi, { doc, ...place })
            }
            return group
        }, mark.style === 'underline' ? { color: mark.hex, width: 2 } : { color: mark.hex })
    })
    view.addEventListener('create-overlay', ({ detail: { index } }) => {
        for (const cfi of sections.get(index) ?? []) {
            const mark = marks.get(cfi)
            if (mark) drawSafely(mark)
        }
    })
    view.addEventListener('show-annotation', ({ detail: { value, index } }) => {
        const content = view.renderer.getContents().find(c => c.index === index)
        if (content) openMark(value, content.doc, lastClick.x, lastClick.y)
    })

    return {
        // Called for every section that loads. Capturing keeps links and the like from reacting in marking mode.
        attach(doc) {
            for (const [cfi, sign] of signs) if (!sign.doc.defaultView) signs.delete(cfi) // the chapter was left
            doc.addEventListener('mousedown', e => {
                if (marking) e.preventDefault() // no selection by a double click
            }, true)
            doc.addEventListener('click', e => {
                lastClick = { x: e.clientX, y: e.clientY }
                if (!marking) {
                    const sign = signAt(doc, e.clientX, e.clientY)
                    if (!sign) return
                    e.preventDefault()
                    e.stopPropagation()
                    return openMark(sign, doc, e.clientX, e.clientY, true)
                }
                e.preventDefault()
                e.stopPropagation()
                onClick(doc, e.clientX, e.clientY)
            }, true)
        },
        setMarking(on, nextHex) {
            marking = on
            hex = nextHex ?? hex
            if (on) view.deselect()
            else {
                clearPending()
                clearCursor()
            }
        },
        cancelPending() {
            clearPending()
            drawCursor()
        },
        // One after the other: a held-down key must not overtake itself while a page turns.
        key(name) {
            keys = keys.then(() => onKey(name)).catch(error => console.warn('Marking key', name, error))
            return keys
        },
        async setAll(list) {
            for (const cfi of [...marks.keys()]) {
                if (!list.some(mark => mark.cfi === cfi)) await this.drop(cfi)
            }
            for (const mark of list) await this.put(mark)
        },
        async put(mark) {
            marks.set(mark.cfi, mark)
            await drawSafely(mark)
        },
        async drop(cfi) {
            marks.delete(cfi)
            signs.delete(cfi)
            for (const set of sections.values()) set.delete(cfi)
            await view.deleteAnnotation({ value: cfi }).catch(() => {})
        },
        get marking() { return marking },
        get pending() { return Boolean(pending) },
        get cursor() {
            return cursor ? { text: cursor.toString(), onScreen: onScreen(cursor) } : null
        },
        get signs() {
            return [...signs.values()].filter(({ doc }) => contentOf(doc)).map(({ x, y, size }) => ({ x, y, size }))
        },
    }
}
