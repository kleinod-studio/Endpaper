// Marks in the text, made without dragging: in marking mode one click on the first word and one on
// the last mark everything between them, also across pages. Two clicks on one word mark that word.
import { Overlayer } from './foliate-js/overlayer.js'

const PENDING = 'endpaper-pending'
const POINT_TOLERANCE = 2
const CLOSING = /^[\p{Pe}\p{Pf}\p{Po}]+/u
const OPENING = /[\p{Ps}\p{Pi}]+$/u

const hits = (range, x, y) => [...range.getClientRects()].some(rect =>
    x >= rect.left - POINT_TOLERANCE && x <= rect.right + POINT_TOLERANCE
    && y >= rect.top - POINT_TOLERANCE && y <= rect.bottom + POINT_TOLERANCE)

// The word under the pointer, or null in the margin or on a picture. On punctuation, the word before it.
const wordAt = (doc, x, y) => {
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
const NOTE_SIZE = 12
const NOTE_GAP = 6
const NOTE_REACH = 3 // a little more room around it for the click

const svgElement = (tag, attributes) => {
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
    const y = first.top + (first.height - NOTE_SIZE) / 2
    if (view.getComputedStyle(block).direction === 'rtl') return { x: box.right + NOTE_GAP, y }
    return { x: box.left - NOTE_GAP - NOTE_SIZE, y }
}

const noteSign = ({ x, y }, color) => {
    const sign = svgElement('g', { class: 'endpaper-note', transform: `translate(${x} ${y})` })
    const fold = NOTE_SIZE / 3
    const end = NOTE_SIZE - fold
    sign.append(
        svgElement('path', { d: `M0 0H${NOTE_SIZE}V${end}L${end} ${NOTE_SIZE}H0Z`, fill: color, stroke: 'rgba(0, 0, 0, .35)', 'stroke-width': '.75' }),
        svgElement('path', { d: `M${NOTE_SIZE} ${end}H${end}V${NOTE_SIZE}Z`, fill: 'rgba(0, 0, 0, .3)' }),
    )
    return sign
}

const quoteOf = range => range.toString().replace(/­/g, '').replace(/\s+/g, ' ').trim()

export const createMarks = (view, post) => {
    const marks = new Map() // by CFI: { cfi, hex, style, hasNote }
    const signs = new Map() // by CFI: where the note sign of a mark is drawn
    const sections = new Map() // section index → the CFIs in it
    let marking = false
    let hex = '#f5c211'
    let pending = null
    let lastClick = { x: 0, y: 0 }

    const contentOf = doc => view.renderer.getContents().find(c => c.doc === doc)

    const clearPending = () => {
        if (!pending) return
        contentOf(pending.doc)?.overlayer?.remove(PENDING)
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

    const onClick = (doc, x, y) => {
        const word = wordAt(doc, x, y)
        if (!word) return
        if (!pending) return startAt(doc, word)
        if (pending.doc !== doc) {
            post('mark-other-chapter')
            return startAt(doc, word)
        }
        const order = pending.word.compareBoundaryPoints(Range.START_TO_START, word)
        if (order === 0) return finish(doc, word)
        finish(doc, order < 0 ? span(pending.word, word) : span(word, pending.word))
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
            if (sign.doc === doc && x >= sign.x - NOTE_REACH && x <= sign.x + NOTE_SIZE + NOTE_REACH
                && y >= sign.y - NOTE_REACH && y <= sign.y + NOTE_SIZE + NOTE_REACH) return cfi
        }
        return null
    }

    const openMark = (cfi, doc, x, y) => {
        const frame = doc.defaultView.frameElement?.getBoundingClientRect()
        post('mark-clicked', { cfi, x: Math.round(x + (frame?.left ?? 0)), y: Math.round(y + (frame?.top ?? 0)) })
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
                    return openMark(sign, doc, e.clientX, e.clientY)
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
            else clearPending()
        },
        cancelPending: clearPending,
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
        get signs() {
            return [...signs.values()].filter(({ doc }) => contentOf(doc)).map(({ x, y }) => ({ x, y, size: NOTE_SIZE }))
        },
    }
}
