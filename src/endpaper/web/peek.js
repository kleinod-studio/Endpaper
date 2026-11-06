// A small popup with the text behind a footnote or cross-reference, so that the reader can stay on the page.
// Books are not trusted: what is shown is rebuilt from text and a few plain tags, nothing is copied as it is.

const EPUB = 'http://www.idpf.org/2007/ops'
const NOTE_TYPES = new Set(['footnote', 'endnote', 'rearnote', 'note'])
export const HIDDEN = 'data-endpaper-note' // reader.js hides what carries this
const SHOW_DELAY = 250
const HIDE_DELAY = 300
const GAP = 8
const SIDE_GAP = 14
const MIN_SIDE_WIDTH = 240
const ARROWS = 52 // room for the page buttons at the window's sides, which lie above the web view
const ARROWS_FROM_WIDTH = 560
const MAX_CHARS = 1500
const MAX_EXCERPT_CHARS = 6000 // a whole excerpt from another publication, and worth reading right here
const MAX_SHORT_LINK = 4 // characters of a link that is a footnote mark, like "1" or "[12]"
const MAX_NOTE_LENGTH = 3000
const FOLLOWING_BLOCKS = 4 // a text cut up by anchors, like verses, may run over a few paragraphs
const FOLLOWING_CHARS = 600
const CACHED_DOCUMENTS = 8

const BLOCKS = 'p,div,li,dd,dt,blockquote,td,th,section,article,aside,figcaption,h1,h2,h3,h4,h5,h6'
const INLINE = { em: 'em', i: 'em', strong: 'strong', b: 'strong', sup: 'sup', sub: 'sub' }
const LEFT_OUT = new Set(['script', 'style', 'template', 'nav', 'img', 'svg', 'audio', 'video', 'object', 'embed', 'iframe'])

const types = element => (element.getAttributeNS(EPUB, 'type') ?? element.getAttribute('epub:type') ?? '').split(/\s+/)
const isNoteLink = a => types(a).includes('noteref') || a.getAttribute('role') === 'doc-noteref'
const hasText = node => node.textContent.trim() !== ''
// The mark of a footnote, like "*" or "12", means nothing in a preview. A longer link marked as a note, as books
// from jw.org mark their Bible references, is part of the sentence: its words stay, as plain text.
const isNoteMark = a => isNoteLink(a) && a.textContent.trim().length <= MAX_SHORT_LINK
const findTarget = (doc, id) => doc.getElementById(id) ?? doc.querySelector(`[name="${CSS.escape(id)}"]`)

// A note is left out of the running text when a book says so, or when it and its mark link to each other.
const linksBack = (note, a) => {
    const before = a.previousSibling
    const names = [a.id, a.parentElement?.id, before?.nodeType === 1 && !hasText(before) ? before.id : null].filter(Boolean)
    return [...note.querySelectorAll('a[href^="#"]')].some(link => names.includes(link.getAttribute('href').slice(1)))
}

const looksLikeNote = (a, target) =>
    a.textContent.trim().length <= MAX_SHORT_LINK
    && target.matches('p,div,li,dd')
    && !target.querySelector('h1,h2,h3,h4,h5,h6')
    && target.textContent.length <= MAX_NOTE_LENGTH
    && linksBack(target, a)

export const hideNotes = doc => {
    for (const aside of doc.querySelectorAll('aside'))
        if (types(aside).some(type => NOTE_TYPES.has(type))) aside.setAttribute(HIDDEN, '')
    for (const a of doc.querySelectorAll('a[href^="#"]')) {
        const target = findTarget(doc, decodeURI(a.getAttribute('href').slice(1)))
        if (!target || !hasText(target) || target.contains(a)) continue
        if (isNoteLink(a) || looksLikeNote(a, target)) target.setAttribute(HIDDEN, '')
    }
}

// An empty anchor ends the text before it, as the next verse does, unless it only belongs to the link after it.
const isBoundary = element =>
    element.id && !hasText(element) && !element.children.length && element.nextSibling?.nodeName?.toLowerCase() !== 'a'

const textUpTo = (start, boundary) => {
    const range = start.cloneRange()
    range.setEndBefore(boundary)
    return range.toString()
}

const rangeFromAnchor = anchor => {
    const doc = anchor.ownerDocument
    const range = doc.createRange()
    range.setStartAfter(anchor)
    let block = anchor.closest(BLOCKS) ?? doc.body
    range.setEnd(block, block.childNodes.length)
    for (let tries = 0; !range.toString().trim() && tries < FOLLOWING_BLOCKS; tries++) { // nothing behind the anchor here
        if (!block.nextElementSibling) break
        block = block.nextElementSibling
        range.setEnd(block, block.childNodes.length)
    }
    const stopIn = element => {
        const candidates = element.matches?.('[id]') ? [element] : []
        for (const candidate of [...candidates, ...element.querySelectorAll('[id]')]) {
            const after = anchor.compareDocumentPosition(candidate) & Node.DOCUMENT_POSITION_FOLLOWING
            if (after && isBoundary(candidate) && textUpTo(range, candidate).trim()) return candidate
        }
        return null
    }
    let boundary = stopIn(block)
    let sibling = block
    let chars = 0
    for (let n = 0; !boundary && n < FOLLOWING_BLOCKS && chars < FOLLOWING_CHARS; n++) {
        sibling = sibling.nextElementSibling
        if (!sibling) break
        chars += sibling.textContent.length
        boundary = stopIn(sibling)
    }
    if (boundary) range.setEndBefore(boundary)
    return range
}

const buildFrom = (source, out, state) => {
    for (const node of source.childNodes) {
        if (state.left <= 0) return
        if (node.nodeType === Node.TEXT_NODE) {
            let text = node.data.slice(0, state.left)
            state.left -= text.length
            if (text.length < node.data.length) {
                state.cut = true
                text = text.replace(/\s+\S*$/, '') // not in the middle of a word
            }
            out.append(text)
            continue
        }
        if (node.nodeType !== Node.ELEMENT_NODE) continue
        const name = node.localName.toLowerCase()
        if (LEFT_OUT.has(name) || isNoteMark(node) || node.hasAttribute(HIDDEN)) continue
        if (name === 'br') {
            out.append(document.createElement('br'))
        } else if (INLINE[name]) {
            const inline = document.createElement(INLINE[name])
            buildFrom(node, inline, state)
            out.append(inline)
        } else if (node.matches(BLOCKS) && !node.querySelector(BLOCKS)) {
            const paragraph = document.createElement('p')
            if (/^h[1-6]$/.test(name)) paragraph.className = 'heading'
            buildFrom(node, paragraph, state)
            if (paragraph.textContent.trim()) out.append(paragraph)
        } else {
            buildFrom(node, out, state)
        }
    }
}

// An excerpt from another publication: the anchor sits in a title line that links back, and the excerpt is what
// surrounds that line. Such a title is often framed with marks, like ***Title***.
const excerptAround = target => {
    if (hasText(target)) return null
    const link = target.closest('a[href]')
    const line = link?.closest(BLOCKS)
    const whole = line?.parentElement
    if (!whole || whole.localName === 'body' || line.textContent.trim() !== link.textContent.trim()) return null
    return whole.textContent.trim().length > line.textContent.trim().length ? { whole, line } : null
}

const contentOfExcerpt = ({ whole, line }, out, state) => {
    const title = document.createElement('p')
    title.className = 'heading'
    title.textContent = line.textContent.replace(/^[^\p{L}\p{N}]+|[*\s]+$/gu, '')
    out.append(title)
    const marked = 'data-endpaper-title'
    line.setAttribute(marked, '')
    const rest = whole.cloneNode(true)
    line.removeAttribute(marked)
    rest.querySelector(`[${marked}]`)?.remove()
    buildFrom(rest, out, state)
}

const contentOf = target => {
    const out = document.createDocumentFragment()
    const excerpt = excerptAround(target)
    const state = { left: excerpt ? MAX_EXCERPT_CHARS : MAX_CHARS, cut: false }
    if (excerpt) {
        contentOfExcerpt(excerpt, out, state)
    } else if (hasText(target)) {
        const holder = target.ownerDocument.createElement('div')
        const copy = target.cloneNode(true)
        copy.removeAttribute(HIDDEN) // the note itself is what is wanted here
        holder.append(copy)
        if (/^h[1-6]$/i.test(target.localName) && target.nextElementSibling)
            holder.append(target.nextElementSibling.cloneNode(true))
        buildFrom(holder, out, state)
    } else {
        buildFrom(rangeFromAnchor(target).cloneContents(), out, state)
    }
    if (state.cut) (out.lastElementChild ?? out).append(' …')
    return out
}

export const createPeek = (view, { theme, isMarking, label }) => {
    const box = document.createElement('div')
    box.className = 'endpaper-peek'
    box.hidden = true
    box.setAttribute('role', 'note')
    const body = document.createElement('div')
    body.className = 'body'
    const go = document.createElement('button')
    go.type = 'button'
    go.hidden = true
    box.append(body, go)
    document.body.append(box)

    const documents = new Map()
    let current = null // the link being shown or about to be
    let pinned = false
    let target = null
    let showTimer = 0
    let hideTimer = 0
    let lastPointer = 'mouse'
    let pressing = false

    const sectionDocument = index => {
        const live = view.renderer.getContents().find(content => content.index === index)?.doc
        if (live) return live
        if (!documents.has(index)) {
            documents.set(index, Promise.resolve(view.book.sections[index].createDocument()).then(doc => {
                hideNotes(doc)
                return doc
            }))
            if (documents.size > CACHED_DOCUMENTS) documents.delete(documents.keys().next().value)
        }
        return documents.get(index)
    }

    const resolve = (a, index) => {
        const raw = a.getAttribute('href')
        const href = view.book.sections[index]?.resolveHref?.(raw) ?? raw
        if (view.book.isExternal?.(href)) return null
        const [path, hash] = href.split('#')
        if (!hash) return null // a whole chapter is nothing to preview
        const place = view.book.resolveHref?.(path)
        return place && place.index >= 0 ? { href, index: place.index, id: decodeURI(hash) } : null
    }

    // Beside the link, on whichever side it fits, so that the line being read stays free.
    const place = a => {
        const rect = a.getClientRects()[0]
        if (!rect) return false
        const frame = a.ownerDocument.defaultView.frameElement?.getBoundingClientRect()
        const left = rect.left + (frame?.left ?? 0)
        const top = rect.top + (frame?.top ?? 0)
        const edge = window.innerWidth >= ARROWS_FROM_WIDTH ? ARROWS : GAP
        const rightRoom = window.innerWidth - edge - (left + rect.width) - SIDE_GAP
        const leftRoom = left - edge - SIDE_GAP
        box.style.maxWidth = ''
        const wanted = box.offsetWidth
        const side = rightRoom >= wanted ? 'right' : leftRoom >= wanted ? 'left' : rightRoom >= leftRoom ? 'right' : 'left'
        const room = side === 'right' ? rightRoom : leftRoom
        box.dataset.side = room >= MIN_SIDE_WIDTH ? side : 'over'
        if (room < MIN_SIDE_WIDTH) { // a narrow window: above or below it is
            const { offsetWidth: width, offsetHeight: height } = box
            const above = top - height - GAP
            box.style.top = `${above >= GAP ? above : Math.min(top + rect.height + GAP, window.innerHeight - height - GAP)}px`
            box.style.left = `${Math.max(GAP, Math.min(left + rect.width / 2 - width / 2, window.innerWidth - width - GAP))}px`
            return true
        }
        if (room < wanted) box.style.maxWidth = `${room}px`
        const { offsetWidth: width, offsetHeight: height } = box
        box.style.left = `${side === 'right' ? left + rect.width + SIDE_GAP : left - SIDE_GAP - width}px`
        const middle = top + rect.height / 2 - height / 2
        box.style.top = `${Math.max(GAP, Math.min(middle, window.innerHeight - height - GAP))}px`
        return true
    }

    const hide = () => {
        clearTimeout(showTimer)
        clearTimeout(hideTimer)
        current = target = null
        pinned = false
        box.hidden = true
        body.replaceChildren()
    }

    const show = async (a, index, pin) => {
        const place_ = resolve(a, index)
        // While marking, a click on a link marks its word: the preview comes on hover only.
        if (!place_ || (pin && isMarking())) return false
        current = a
        let found = null
        try {
            found = findTarget(await sectionDocument(place_.index), place_.id)
        } catch {
            found = null
        }
        if (current !== a) return true // meanwhile the pointer went on
        const content = found && contentOf(found)
        if (!content || !content.textContent.trim()) {
            current = null
            return false
        }
        const style = getComputedStyle(a.ownerDocument.body)
        const colors = theme()
        box.style.setProperty('--surface', colors.peek)
        box.style.color = colors.fg
        box.style.colorScheme = colors.scheme
        // The book's font where it names one. Its default is the web view's default, which reader_view.py leaves at sans-serif.
        const family = getComputedStyle(a).fontFamily
        box.style.fontFamily = family === '-webkit-standard' ? 'sans-serif' : family
        box.style.fontSize = `${parseFloat(style.fontSize) * 0.92}px`
        box.lang = a.ownerDocument.documentElement.lang
        body.replaceChildren(content)
        body.scrollTop = 0
        pinned = pin
        target = place_.href
        go.textContent = label()
        go.hidden = !pin || Boolean(found.closest(`[${HIDDEN}]`))
        // While marking it lets clicks through to the words below it, and goes with the pointer.
        box.style.pointerEvents = isMarking() ? 'none' : ''
        box.hidden = false
        if (!place(a)) hide()
        return true
    }

    const later = (fn, delay, keep) => {
        clearTimeout(showTimer)
        clearTimeout(hideTimer)
        const timer = setTimeout(fn, delay)
        if (keep === 'show') showTimer = timer
        else hideTimer = timer
    }

    const leave = () => {
        if (!pinned) later(hide, HIDE_DELAY)
    }

    box.addEventListener('mouseenter', () => clearTimeout(hideTimer))
    box.addEventListener('mouseleave', leave)
    go.addEventListener('click', () => {
        const href = target
        hide()
        if (href) view.goTo(href)
    })
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') hide()
    })
    // Moving to a link with the Tab key also reports a location, without the page having changed.
    let location = null
    view.addEventListener('relocate', ({ detail }) => {
        if (detail.cfi !== location) hide()
        location = detail.cfi
    })
    // What stands outside the reading order, or an excerpt, is read right here. A place in the text is gone to.
    const staysHere = async (a, place_) => {
        if (isNoteLink(a) || lastPointer === 'touch' || view.book.sections[place_.index]?.linear === 'no') return true
        try {
            const found = findTarget(await sectionDocument(place_.index), place_.id)
            return Boolean(found && excerptAround(found))
        } catch {
            return false
        }
    }

    view.addEventListener('link', e => {
        if (isMarking()) return
        const { a, href } = e.detail
        const index = view.renderer.getContents().find(content => content.doc === a.ownerDocument)?.index
        const place_ = resolve(a, index)
        if (!place_) {
            hide()
            return
        }
        e.preventDefault()
        staysHere(a, place_).then(async stay => {
            if (stay && await show(a, index, true)) return
            hide() // without anything to show, the link does what links do
            view.goTo(href)
        })
    })

    return {
        hide,
        attach(doc, index) {
            hideNotes(doc)
            const linkOf = e => e.target.closest?.('a[href]')
            doc.addEventListener('pointerdown', e => {
                lastPointer = e.pointerType || 'mouse'
                pressing = true
                if (isMarking()) hide() // a word was chosen: the preview must not cover what comes next
            }, true)
            doc.addEventListener('pointerup', () => { pressing = false }, true)
            doc.addEventListener('mouseover', e => {
                const a = linkOf(e)
                if (!a || pinned) return
                if (a === current && !box.hidden) {
                    clearTimeout(hideTimer)
                    return
                }
                if (resolve(a, index)) later(() => show(a, index, false), SHOW_DELAY, 'show')
            })
            doc.addEventListener('mouseout', e => {
                const a = linkOf(e)
                if (a && !a.contains(e.relatedTarget)) {
                    clearTimeout(showTimer)
                    leave()
                }
            })
            doc.addEventListener('focusin', e => {
                const a = linkOf(e)
                // Reached with the Tab key. Later: the page may first have to turn to where the link is.
                if (a && !pinned && !pressing) later(() => show(a, index, false), SHOW_DELAY, 'show')
            })
            doc.addEventListener('focusout', e => {
                if (linkOf(e)) leave()
            })
            doc.addEventListener('click', e => {
                if (pinned && !linkOf(e)) hide()
            }, true)
            doc.addEventListener('keydown', e => {
                if (e.key === 'Escape') hide()
            })
        },
        get state() {
            return {
                visible: !box.hidden,
                side: box.dataset.side,
                box: box.hidden ? null : (({ left, right, top, bottom }) => ({ left, right, top, bottom }))(box.getBoundingClientRect()),
                pinned,
                text: body.textContent,
                tags: [...body.querySelectorAll('*')].map(element => element.localName),
                go: !go.hidden,
                inWindow: !box.hidden && box.getBoundingClientRect().top >= 0
                    && box.getBoundingClientRect().bottom <= window.innerHeight,
            }
        },
        clickGo: () => go.click(),
    }
}
