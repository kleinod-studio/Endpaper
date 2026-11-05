// Bookmarks on paragraphs: a small ribbon in the margin. They lie on the overlayer of foliate-js like the marks,
// but are drawn by place and let clicks through, so that a mark on the same words stays clickable.
import { firstOnScreen, firstRect, onScreen, quoteOf, rangeIn, shown, svgElement, wordAt, wordsOf } from './marks.js'

const KEY = 'endpaper-bookmark '
const COLOR = '#e5484d'
const LINE_SHARE = 0.8 // height of the ribbon, as a share of the height of the line
const MIN_HEIGHT = 12
const MAX_HEIGHT = 20
const WIDTH_SHARE = 0.65
const GAP = 12
const EDGE = 2
const REACH = 4
const MAX_QUOTE = 160

const blockOf = node => {
    let block = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement
    const window = block.ownerDocument.defaultView
    while (block.parentElement && window.getComputedStyle(block).display === 'inline') block = block.parentElement
    return block
}

const firstWordIn = block => {
    const walker = block.ownerDocument.createTreeWalker(block, NodeFilter.SHOW_TEXT)
    while (walker.nextNode()) {
        const words = shown(walker.currentNode) ? wordsOf(walker.currentNode) : []
        if (words.length) return rangeIn(walker.currentNode, words[0])
    }
    return null
}

const quoteOfBlock = (block, word) => {
    const range = word.cloneRange()
    range.setEnd(block, block.childNodes.length)
    const text = quoteOf(range)
    return text.length > MAX_QUOTE ? `${text.slice(0, MAX_QUOTE)}…` : text
}

const place = range => {
    const first = firstRect(range)
    if (!first) return null
    const block = blockOf(range.startContainer)
    const line = first.top + first.height / 2
    const box = [...block.getClientRects()].find(rect => line >= rect.top && line <= rect.bottom) ?? first
    const window = block.ownerDocument.defaultView
    const height = Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, Math.round(first.height * LINE_SHARE)))
    const width = Math.round(height * WIDTH_SHARE)
    const y = first.top + (first.height - height) / 2
    const x = window.getComputedStyle(block).direction === 'rtl'
        ? Math.max(EDGE, box.left - GAP - width)
        : box.right + GAP
    return { x, y, width, height, textRight: box.right }
}

const ribbon = ({ x, y, width, height }) => svgElement('path', {
    class: 'endpaper-bookmark',
    fill: COLOR,
    d: `M${x} ${y}h${width}v${height}l${-width / 2} ${-width / 2.5}l${-width / 2} ${width / 2.5}z`,
})

export const createBookmarks = (view, post) => {
    let list = []
    let placing = false
    const signs = new Map() // by CFI

    const contentOf = doc => view.renderer.getContents().find(content => content.doc === doc)

    const drawOn = content => {
        if (!content?.overlayer) return
        for (const [cfi, sign] of signs) {
            if (sign.doc !== content.doc) continue
            content.overlayer.remove(KEY + cfi)
            signs.delete(cfi)
        }
        for (const cfi of list) {
            let range = null
            try {
                const found = view.resolveNavigation(cfi)
                if (found?.index === content.index) range = found.anchor(content.doc)
            } catch {
                // A bookmark that no longer fits the book must not get in the way. It stays in the notes file.
            }
            if (!range?.getClientRects) continue
            // No rectangles of its own, so that clicks on the words reach what is there.
            content.overlayer.add(KEY + cfi, { getClientRects: () => [] }, () => {
                const where = place(range)
                if (!where) return svgElement('g', {})
                signs.set(cfi, { doc: content.doc, range, ...where })
                return ribbon(where)
            })
        }
    }

    const drawAll = () => view.renderer.getContents().forEach(drawOn)

    const signAt = (doc, x, y) => {
        for (const [cfi, sign] of signs) {
            if (sign.doc === doc && x >= sign.x - REACH && x <= sign.x + sign.width + REACH
                && y >= sign.y - REACH && y <= sign.y + sign.height + REACH) return cfi
        }
        return null
    }

    const send = (doc, word, block, clicked) => {
        const content = contentOf(doc)
        if (!content || !word) return
        post('bookmark-place', {
            cfi: view.getCFI(content.index, word),
            quote: quoteOfBlock(block, word),
            chapter: view.getProgressOf(content.index, word)?.tocItem?.label?.trim() ?? null,
            clicked,
        })
    }

    view.addEventListener('create-overlay', ({ detail: { index } }) => {
        // The overlayer is only in place after this event.
        requestAnimationFrame(() => drawOn(view.renderer.getContents().find(content => content.index === index)))
    })

    return {
        attach(doc) {
            for (const [cfi, sign] of signs) if (!sign.doc.defaultView) signs.delete(cfi) // the chapter was left
            doc.addEventListener('mousedown', e => {
                if (placing) e.preventDefault()
            }, true)
            doc.addEventListener('click', e => {
                if (placing) {
                    e.preventDefault()
                    e.stopPropagation()
                    const under = wordAt(doc, e.clientX, e.clientY)?.startContainer ?? doc.elementFromPoint(e.clientX, e.clientY)
                    if (!under || under === doc.documentElement || under === doc.body) return
                    const block = blockOf(under)
                    send(doc, firstWordIn(block), block, true)
                    return
                }
                const cfi = signAt(doc, e.clientX, e.clientY)
                if (!cfi) return
                e.preventDefault()
                e.stopPropagation()
                post('bookmark-clicked', { cfi })
            }, true)
        },
        // The first paragraph on the page, or the first line here if a paragraph comes over from the page before.
        here() {
            const { doc } = view.renderer.getContents()[0] ?? {}
            const word = doc && firstOnScreen(doc)
            if (!word) return
            const block = blockOf(word.startContainer)
            const start = firstWordIn(block)
            const chosen = start && onScreen(start) ? start : word
            send(doc, chosen, block, false)
        },
        setPlacing(on) {
            placing = on
        },
        setAll(next) {
            list = next
            drawAll()
        },
        get state() {
            return {
                placing,
                signs: [...signs.entries()].filter(([, { doc }]) => contentOf(doc)).map(([cfi, sign]) => ({
                    cfi,
                    x: sign.x,
                    y: sign.y,
                    textRight: sign.textRight,
                    onPage: onScreen(sign.range),
                })),
            }
        },
    }
}
