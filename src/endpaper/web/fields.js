// The fields of a study edition. What is typed goes to the notes file: the book itself is never changed.

const SELECTOR = 'textarea, input[type="checkbox"], input[type="text"], input:not([type])'
const MAX_QUESTION = 300
const MAX_SHARE_OF_WINDOW = 0.6 // a field cannot break across pages: taller than this, it scrolls inside
const FOLLOW_DELAY = 250
const SKIP = '[data-endpaper-note], script, style'

const clean = text => text.replace(/\s+/g, ' ').trim()

// A name can be used twice, and many books have neither name nor id: a field is known by its place then.
const fieldsOf = (doc, section) => {
    const counts = new Map()
    return [...doc.querySelectorAll(SELECTOR)].map(element => {
        const name = element.getAttribute('name') || element.id || element.localName
        const count = (counts.get(name) ?? 0) + 1
        counts.set(name, count)
        return { element, key: `${section}#${name}${count > 1 ? `~${count}` : ''}` }
    })
}

const labelOf = element => {
    const label = element.id ? element.ownerDocument.querySelector(`label[for="${CSS.escape(element.id)}"]`) : null
    return clean((label ?? element.closest('label'))?.textContent ?? '')
}

// What stands before the field: the question, as far as a book can tell.
const questionBefore = element => {
    for (let node = element; node && node !== element.ownerDocument.body; node = node.parentElement) {
        for (let previous = node.previousElementSibling; previous; previous = previous.previousElementSibling) {
            if (previous.matches(SKIP) || previous.matches('label')) continue
            const text = clean(previous.textContent)
            if (text) return text.length > MAX_QUESTION ? `${text.slice(0, MAX_QUESTION)}…` : text
        }
    }
    return ''
}

const grow = element => {
    if (element.localName !== 'textarea') return
    const most = window.innerHeight * MAX_SHARE_OF_WINDOW
    element.style.setProperty('height', 'auto', 'important')
    const needed = element.scrollHeight + 2
    element.style.setProperty('height', `${Math.min(needed, most)}px`, 'important')
    element.style.setProperty('overflow-y', needed > most ? 'auto' : 'hidden', 'important')
}

const isOnPage = element => {
    const frame = element.ownerDocument.defaultView.frameElement?.getBoundingClientRect()
    const { left, right } = element.getBoundingClientRect()
    return left + (frame?.left ?? 0) >= 0 && right + (frame?.left ?? 0) <= window.innerWidth
}

const isCheck = element => element.type === 'checkbox'

// Markdown in an answer: shown formatted while the field is not being typed in, as the notes in the flyout.
// The formatted text lies over the field, as a child of <html> outside <body>: in the body it would shift the
// places (CFIs) of the marks after it, and the marks and the search would take it for text of the book.
export const FORMATTED = 'data-endpaper-formatted'
const MARKDOWN = /(\*\*|__|~~|`)\S|(^|[^\p{L}\p{N}*])\*[^\s*]|(^|[^\p{L}\p{N}_])_[^\s_]|^[ \t]*([-*+]|\d{1,9}[.)])[ \t]|^ {0,3}#{1,6}[ \t]|^[ \t]*>/mu
const INLINE = /(\*\*|__)(?=\S)([^]*?\S)\1|~~(?=\S)([^]*?\S)~~|`([^`\n]+)`|\*(?=[^\s*])([^]*?[^\s*])\*|(?<![\p{L}\p{N}_])_(?=[^\s_])([^]*?[^\s_])_(?![\p{L}\p{N}_])/gu
const LIST = /^([ \t]*)([-*+]|\d{1,9}[.)])[ \t]+(?:\[([ xX])\][ \t]+)?(.*)$/
const HEADING = /^ {0,3}#{1,6}[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$/
const QUOTE = /^[ \t]*>[ \t]?(.*)$/
const COPIED = ['font-family', 'font-size', 'line-height', 'letter-spacing', 'color', 'text-align', 'direction',
    'padding-top', 'padding-right', 'padding-bottom', 'padding-left']

// Book styles cannot reach these spans: they stand outside <body>, and every look is set here.
const styled = (doc, styles) => {
    const span = doc.createElement('span')
    for (const [name, value] of Object.entries(styles)) span.style.setProperty(name, value, 'important')
    return span
}

// Only text nodes and spans: nothing from an answer is ever read as HTML.
const inline = (doc, text, parent) => {
    let last = 0
    for (const match of text.matchAll(INLINE)) {
        parent.append(text.slice(last, match.index))
        const [, , bold, strike, code, star, underscore] = match
        let span
        if (bold !== undefined) inline(doc, bold, span = styled(doc, { 'font-weight': 'bold' }))
        else if (strike !== undefined) inline(doc, strike, span = styled(doc, { 'text-decoration': 'line-through' }))
        else if (code !== undefined) {
            span = styled(doc, { 'font-family': 'monospace', 'font-size': '.9em' })
            span.textContent = code
        } else inline(doc, star ?? underscore, span = styled(doc, { 'font-style': 'italic' }))
        parent.append(span)
        last = match.index + match[0].length
    }
    parent.append(text.slice(last))
}

const formatted = (doc, text, multiline) => {
    const fragment = doc.createDocumentFragment()
    if (!multiline) {
        inline(doc, text, fragment)
        return fragment
    }
    for (const line of text.split('\n')) {
        const block = styled(doc, { display: 'block' })
        let item, heading, quote
        if (!line.trim()) block.append('​') // an empty line keeps its height
        else if ((heading = HEADING.exec(line))) {
            block.style.setProperty('font-weight', 'bold', 'important')
            inline(doc, heading[1], block)
        } else if ((item = LIST.exec(line))) {
            const [, indent, marker, task, rest] = item
            const level = Math.floor(indent.replace(/\t/g, '  ').length / 2)
            block.style.setProperty('padding-inline-start', `${1.3 + level * 1.3}em`, 'important')
            block.style.setProperty('text-indent', '-1.3em', 'important')
            const sign = task !== undefined ? (task === ' ' ? '☐' : '☑') : /\d/.test(marker) ? marker : '•'
            const mark = styled(doc, { display: 'inline-block', 'min-width': '1.3em', 'text-indent': '0' })
            mark.textContent = sign
            block.append(mark)
            inline(doc, rest, block)
        } else if ((quote = QUOTE.exec(line))) {
            block.style.setProperty('font-style', 'italic', 'important')
            block.style.setProperty('border-inline-start', '2px solid rgba(128, 128, 128, .6)', 'important')
            block.style.setProperty('padding-inline-start', '.6em', 'important')
            inline(doc, quote[1], block)
        } else inline(doc, line, block)
        fragment.append(block)
    }
    return fragment
}

const hideFormatted = item => {
    item.overlay?.remove()
    item.overlay = null
    item.element.endpaperFormatted = null // for the tests
    item.rendered = null
    item.element.style.removeProperty('-webkit-text-fill-color')
}

// Over the field, in its place and look; clicks go through to the field below.
const showFormatted = item => {
    const { element } = item
    const doc = element.ownerDocument
    const text = element.value
    const rect = element.getBoundingClientRect()
    if (isCheck(element) || item.editing || !text || !MARKDOWN.test(text)
        || !element.isConnected || !rect.width || !rect.height) {
        hideFormatted(item)
        return
    }
    if (!item.overlay) {
        item.overlay = doc.createElement('div')
        item.overlay.setAttribute(FORMATTED, '')
        item.overlay.setAttribute('aria-hidden', 'true') // the field itself is what is read out
        doc.documentElement.append(item.overlay)
        element.endpaperFormatted = item.overlay
    }
    const { overlay } = item
    const multiline = element.localName === 'textarea'
    if (item.rendered !== text) {
        overlay.replaceChildren(formatted(doc, text, multiline))
        item.rendered = text
    }
    const style = doc.defaultView.getComputedStyle(element)
    const set = (name, value) => overlay.style.setProperty(name, value, 'important')
    for (const name of COPIED) set(name, style.getPropertyValue(name))
    set('position', 'fixed')
    set('left', `${rect.left + element.clientLeft}px`)
    set('top', `${rect.top + element.clientTop}px`)
    set('width', `${element.clientWidth}px`)
    set('height', `${element.clientHeight}px`)
    set('box-sizing', 'border-box')
    set('margin', '0')
    set('border', '0')
    set('background', 'none')
    set('overflow', 'hidden')
    set('white-space', multiline ? 'pre-wrap' : 'pre')
    set('overflow-wrap', 'break-word')
    set('pointer-events', 'none')
    set('user-select', 'none')
    set('-webkit-user-select', 'none')
    set('z-index', '1')
    // The field keeps its place and its height; only its own letters step aside.
    element.style.setProperty('-webkit-text-fill-color', 'transparent', 'important')
}

export const createFields = (view, post) => {
    let answers = {}
    let checked = false
    let followTimer = 0
    const fieldsIn = new WeakMap() // must not keep a chapter in memory after the reader has left it

    const show = item => {
        const { element, key } = item
        if (key in answers) {
            if (isCheck(element)) element.checked = answers[key].trim().toLowerCase().startsWith('[x]')
            else if (element.value !== answers[key]) element.value = answers[key]
        } else if (element.dataset.endpaperAnswered) { // its answer was taken away elsewhere
            if (isCheck(element)) element.checked = element.defaultChecked
            else element.value = ''
        }
        if (key in answers) element.dataset.endpaperAnswered = '1'
        else delete element.dataset.endpaperAnswered
        grow(element)
        showFormatted(item)
    }

    // After every new layout the fields may stand elsewhere.
    const refresh = () => {
        for (const { doc } of view.renderer?.getContents?.() ?? []) fieldsIn.get(doc)?.forEach(showFormatted)
    }
    view.addEventListener('relocate', refresh)

    const send = ({ element, key }) => {
        const text = isCheck(element) ? (element.checked ? '[x]' : '') : element.value
        if (text.trim()) {
            answers[key] = text
            element.dataset.endpaperAnswered = '1'
        } else {
            delete answers[key]
            delete element.dataset.endpaperAnswered
        }
        post('answer', {
            field: key,
            text,
            kind: isCheck(element) ? 'check' : 'text',
            question: isCheck(element) ? labelOf(element) || questionBefore(element) : questionBefore(element),
            chapter: view.lastLocation?.tocItem?.label?.trim() ?? null,
        })
    }

    // Answers whose field is not in the book any more must not be forgotten: the flyout shows them.
    const findMissing = async () => {
        const { sections } = view.book
        const missing = []
        const bySection = new Map()
        for (const key of Object.keys(answers)) {
            const section = key.slice(0, key.indexOf('#'))
            bySection.set(section, [...(bySection.get(section) ?? []), key])
        }
        for (const [section, keys] of bySection) {
            const found = sections.find(candidate => candidate.id === section)
            if (!found) {
                missing.push(...keys)
                continue
            }
            try {
                const present = new Set(fieldsOf(await found.createDocument(), section).map(item => item.key))
                missing.push(...keys.filter(key => !present.has(key)))
            } catch {
                // A chapter that cannot be read says nothing about its fields.
            }
        }
        post('answers-missing', { fields: missing })
    }

    return {
        // From the search: the field with this key, brought onto the page and given the focus.
        async goTo(key) {
            const section = key.slice(0, key.indexOf('#'))
            const index = view.book.sections.findIndex(candidate => candidate.id === section)
            if (index < 0) return
            await view.goTo(index)
            const doc = view.renderer.getContents().find(content => content.index === index)?.doc
            const item = doc && fieldsIn.get(doc)?.find(candidate => candidate.key === key)
            if (!item) return
            await view.renderer.scrollToAnchor(item.element)
            item.element.focus({ preventScroll: true })
        },
        attach(doc, index) {
            const section = view.book.sections[index]?.id ?? String(index)
            const items = fieldsOf(doc, section)
            fieldsIn.set(doc, items)
            for (const item of items) {
                show(item)
                if (!isCheck(item.element)) {
                    item.element.addEventListener('focusin', () => {
                        item.editing = true
                        hideFormatted(item)
                    })
                    item.element.addEventListener('focusout', () => {
                        item.editing = false
                        showFormatted(item)
                    })
                }
                item.element.addEventListener(isCheck(item.element) ? 'change' : 'input', () => {
                    grow(item.element)
                    send(item)
                    showFormatted(item)
                    // Grown past the end of the page, a field moves on to the next: the reader goes with it,
                    // once the pages are laid out anew.
                    clearTimeout(followTimer)
                    followTimer = setTimeout(() => {
                        if (!isOnPage(item.element)) view.renderer.scrollToAnchor(item.element)
                    }, FOLLOW_DELAY)
                })
            }
            doc.addEventListener('focusin', e => {
                if (e.target.matches?.(SELECTOR) && !isCheck(e.target)) post('field-focus', { active: true })
            })
            doc.addEventListener('focusout', e => {
                if (e.target.matches?.(SELECTOR) && !isCheck(e.target)) post('field-focus', { active: false })
            })
        },
        refresh,
        setAnswers(next) {
            answers = next
            for (const { doc } of view.renderer.getContents()) fieldsIn.get(doc)?.forEach(show)
            if (!checked || Object.keys(answers).length) {
                checked = true
                findMissing()
            }
        },
    }
}
