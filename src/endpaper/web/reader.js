// The reading area. It runs inside the web view and talks to reader_view.py through messages.
// Books are not trusted: the Content Security Policy set by reader_view.py keeps their scripts from running.
import './foliate-js/view.js'
import { adapt, audit } from './contrast.js'
import { createMarks } from './marks.js'
import { createPeek } from './peek.js'
import { createFields } from './fields.js'

const post = (type, data = {}) =>
    window.webkit.messageHandlers.endpaper.postMessage(JSON.stringify({ type, ...data }))

const THEMES = {
    light: { bg: '#ffffff', fg: '#2e2e32', peek: 'rgba(247, 247, 250, .86)', scheme: 'light', adapt: false },
    sepia: {
        peek: 'rgba(253, 248, 236, .86)',
        bg: '#f5eedc', fg: '#40342a', scheme: 'light', adapt: true, dark: false,
        bgRgb: 'rgb(245, 238, 220)', fgRgb: 'rgb(64, 52, 42)', surfaceRgb: 'rgb(233, 224, 200)',
    },
    sage: {
        peek: 'rgba(244, 249, 241, .86)',
        bg: '#e4ecdf', fg: '#2c3a2e', scheme: 'light', adapt: true, dark: false,
        bgRgb: 'rgb(228, 236, 223)', fgRgb: 'rgb(44, 58, 46)', surfaceRgb: 'rgb(212, 224, 205)',
    },
    dark: {
        peek: 'rgba(58, 58, 66, .86)',
        bg: '#222226', fg: '#e3e3e6', scheme: 'dark', adapt: true, dark: true,
        bgRgb: 'rgb(34, 34, 38)', fgRgb: 'rgb(227, 227, 230)', surfaceRgb: 'rgb(50, 50, 56)',
    },
    black: {
        peek: 'rgba(40, 40, 44, .86)',
        bg: '#000000', fg: '#d6d6d6', scheme: 'dark', adapt: true, dark: true,
        bgRgb: 'rgb(0, 0, 0)', fgRgb: 'rgb(214, 214, 214)', surfaceRgb: 'rgb(32, 32, 32)',
    },
}
const LINE_HEIGHT = { compact: 1.3, normal: 1.5, relaxed: 1.8 }
const TEXT_WIDTH = { narrow: 520, normal: 640, wide: 800 }
// Two pages side by side only where each still has comfortable lines, also with larger text.
const MIN_SPREAD_COLUMN = 400
const SPREAD_COLUMN_SHARE = 0.395 // of the view's width: what margins and the gap leave for one of two columns
const FONT = { serif: 'serif', 'sans-serif': 'sans-serif' }

let view = null
let marks = null
let peek = null
let fields = null
let settings = null
const blocked = []

const onViolation = e => blocked.push(e.effectiveDirective)
document.addEventListener('securitypolicyviolation', onViolation)

const css = () => {
    const theme = THEMES[settings.theme]
    const font = FONT[settings.font]
    return `
    @namespace epub "http://www.idpf.org/2007/ops";
    html { color-scheme: ${theme.scheme}; font-size: ${settings.font_size}% !important; }
    html, body { background: ${theme.bg} !important; color: ${theme.fg} !important; }
    body { font-size: ${settings.font_size}% !important; }
    p, li, blockquote, dd, div {
        line-height: ${LINE_HEIGHT[settings.spacing]} !important;
    }
    p, li, blockquote, dd {
        hyphens: ${settings.hyphenate ? 'auto' : 'manual'} !important;
        -webkit-hyphens: ${settings.hyphenate ? 'auto' : 'manual'} !important;
    }
    ${font ? `body, body *:not(pre, code, kbd, samp, pre *, code *) { font-family: ${font} !important; }` : ''}
    img, svg { max-width: 100%; }
    /* Pictures are kept whole by foliate-js, but not when they sit in a figure that may break across pages. */
    figure { break-inside: avoid !important; }
    /* Footnotes are shown in the popup at their mark, not in the running text. */
    [data-endpaper-note] { display: none !important; }
    /* Answer fields in study editions are saved in the notes file. Other controls would lose what is done with them. */
    input[type]:not([type="checkbox"]):not([type="text"]), select, button { display: none !important; }
    textarea, input[type="text"], input:not([type]) {
        display: block !important;
        box-sizing: border-box !important;
        width: 100% !important;
        margin: .5em 0 !important;
        padding: .5em .7em !important;
        border: 1px solid rgba(128, 128, 128, .45) !important;
        border-radius: 8px !important;
        background: rgba(128, 128, 128, .08) !important;
        color: ${theme.fg} !important;
        font: inherit !important;
        line-height: 1.4 !important;
        visibility: visible !important;
    }
    textarea { min-height: 4.2em !important; resize: none !important; overflow: hidden !important; }
    textarea:focus, input:focus { outline: 2px solid rgba(53, 132, 228, .7) !important; outline-offset: 1px; }
    input[type="checkbox"] { width: 1.1em; height: 1.1em; vertical-align: -.1em; margin-inline-end: .4em; }
    `
}

const columnCount = () => {
    if (settings.flow !== 'paginated') return 1
    const wanted = Math.max(MIN_SPREAD_COLUMN, TEXT_WIDTH[settings.width] * 0.65) * Math.max(1, settings.font_size / 100)
    return window.innerWidth * SPREAD_COLUMN_SHARE >= wanted ? 2 : 1
}

const applyLayout = () => {
    const theme = THEMES[settings.theme]
    document.documentElement.style.background = theme.bg
    document.body.style.background = theme.bg
    if (!view?.renderer) return
    const r = view.renderer
    r.setAttribute('flow', settings.flow)
    r.setAttribute('margin', '60px') // room for the marking bar
    r.setAttribute('gap', '7%')
    r.setAttribute('max-column-count', String(columnCount()))
    r.setAttribute('max-block-size', '100000px')
    r.setAttribute('max-inline-size', `${TEXT_WIDTH[settings.width]}px`) // lays out the pages anew
    r.setStyles?.(css())
    for (const { doc } of r.getContents?.() ?? []) if (doc) adaptColors(doc)
}

let adaptMs = 0
const adaptColors = doc => {
    const start = performance.now()
    adapt(doc, THEMES[settings.theme])
    adaptMs = performance.now() - start
}

const flattenToc = (items, depth = 0, out = []) => {
    for (const item of items ?? []) {
        out.push({ label: (item.label ?? '').trim(), href: item.href ?? '', depth })
        flattenToc(item.subitems, depth + 1, out)
    }
    return out
}

// Page turning
const onKeydown = e => {
    if (e.ctrlKey || e.altKey || e.metaKey) return
    // localName: in a book's XHTML, tagName is in lower case.
    if (e.target?.isContentEditable || /^(input|textarea|select)$/.test(e.target?.localName ?? '')) return
    const paged = settings.flow === 'paginated'
    switch (e.key) {
        case 'ArrowRight': case 'PageDown': view?.goRight(); break
        case 'ArrowLeft': case 'PageUp': view?.goLeft(); break
        case 'ArrowDown': if (!paged) return; view?.next(); break
        case 'ArrowUp': if (!paged) return; view?.prev(); break
        case ' ': if (e.shiftKey) view?.prev(); else view?.next(); break
        case 'Home': view?.goTo(0); break
        case 'Backspace': view?.history.back(); break
        default: return
    }
    e.preventDefault()
}
document.addEventListener('keydown', onKeydown)

let wheelSum = 0
let wheelLockedUntil = 0
const onWheel = e => {
    if (settings.flow !== 'paginated' || e.ctrlKey || e.target.closest?.('.endpaper-peek')) return
    e.preventDefault()
    const now = performance.now()
    if (now < wheelLockedUntil) return
    wheelSum += Math.abs(e.deltaY) >= Math.abs(e.deltaX) ? e.deltaY : e.deltaX
    if (Math.abs(wheelSum) < 40) return
    if (wheelSum > 0) view?.next()
    else view?.prev()
    wheelSum = 0
    wheelLockedUntil = now + 220
}
document.addEventListener('wheel', onWheel, { passive: false })

window.addEventListener('resize', () => {
    const r = view?.renderer
    if (!r || r.getAttribute('max-column-count') === String(columnCount())) return
    r.setAttribute('max-column-count', String(columnCount()))
    r.render()
})

window.endpaper = {
    async open(url, name, nextSettings, position) {
        settings = nextSettings
        applyLayout()
        try {
            const blob = await (await fetch(url)).blob()
            view = document.createElement('foliate-view')
            marks = createMarks(view, post)
            fields = createFields(view, post)
            peek = createPeek(view, {
                theme: () => THEMES[settings.theme],
                isMarking: () => marks.marking,
                label: () => settings.labels.goTo,
            })
            view.addEventListener('relocate', ({ detail }) => post('relocate', {
                cfi: detail.cfi ?? null,
                fraction: Number.isFinite(detail.fraction) ? detail.fraction : 0,
                tocLabel: detail.tocItem?.label?.trim() ?? '',
                tocHref: detail.tocItem?.href ?? '',
                canGoBack: view.history.canGoBack,
            }))
            view.addEventListener('load', ({ detail: { doc, index } }) => {
                doc.addEventListener('securitypolicyviolation', onViolation)
                doc.addEventListener('keydown', onKeydown)
                doc.addEventListener('wheel', onWheel, { passive: false })
                marks.attach(doc)
                peek.attach(doc, index)
                fields.attach(doc, index)
                adaptColors(doc)
            })
            view.addEventListener('external-link', e => {
                e.preventDefault()
                post('external-link', { href: e.detail.href ?? e.detail.a?.getAttribute('href') ?? '' })
            })
            document.body.append(view)
            await view.open(new File([blob], name, { type: 'application/epub+zip' }))
            applyLayout()
            await view.init({ lastLocation: position, showTextStart: true })
            // A saved position that no longer fits the book must not leave an empty page.
            if (!view.lastLocation) await view.goToTextStart()
            post('opened', { toc: flattenToc(view.book.toc) })
        } catch (error) {
            post('error', { message: String(error) })
        }
    },
    applySettings(nextSettings) {
        settings = nextSettings
        applyLayout()
    },
    goToHref: href => view?.goTo(href),
    next: () => view?.next(),
    prev: () => view?.prev(),
    back: () => view?.history.back(),

    // For the automatic tests; none of this is reachable from a book.
    diagnostics: () => ({
        cfi: view?.lastLocation?.cfi ?? null,
        fraction: view?.lastLocation?.fraction ?? null,
        visibleText: view?.lastLocation?.range?.toString().slice(0, 200) ?? '',
        rawFraction: String(view?.lastLocation?.fraction),
        blocked: blocked.length,
        pwned: window.__pwned ?? null,
    }),
    bookDocument: () => {
        const doc = view?.renderer?.getContents?.()[0]?.doc
        return {
            pwned: doc?.documentElement.getAttribute('data-pwned') ?? null,
            text: doc?.body?.textContent.slice(0, 60) ?? '',
            links: doc ? doc.querySelectorAll('a[href]').length : 0,
            background: doc ? getComputedStyle(doc.body).backgroundColor : '',
            color: doc ? getComputedStyle(doc.body).color : '',
            fontSize: doc ? getComputedStyle(doc.documentElement).fontSize : '',
        }
    },
    contrastAudit: () => {
        const doc = view?.renderer?.getContents?.()[0]?.doc
        return doc ? { ...audit(doc, THEMES[settings.theme].bgRgb ?? 'rgb(255, 255, 255)'), adaptMs: Math.round(adaptMs) } : null
    },
    pageLayout: () => {
        const r = view.renderer
        const style = getComputedStyle(r.getContents()[0].doc.documentElement)
        const column = parseFloat(style.columnWidth) + parseFloat(style.columnGap)
        return {
            viewWidth: window.innerWidth,
            columns: column ? Math.round(r.size / column) : 1,
            size: r.size,
            start: r.start,
            text: view.lastLocation?.range?.toString() ?? '',
        }
    },
    setMarking: (on, hex) => {
        peek?.hide()
        marks?.setMarking(on, hex)
    },
    cancelPendingMark: () => marks?.cancelPending(),
    markKey: name => { marks?.key(name) },
    setMarks: list => marks?.setAll(list),
    putMark: mark => marks?.put(mark),
    dropMark: cfi => marks?.drop(cfi),
    // For the tests: a real click on the screen at the place of a word, or anywhere.
    clickAt(x, y, detail = 1) {
        const { doc } = view.renderer.getContents()[0]
        const target = doc.elementFromPoint(x, y) ?? doc.documentElement
        const options = { bubbles: true, cancelable: true, clientX: x, clientY: y, detail, view: doc.defaultView }
        for (const type of ['mousedown', 'mouseup', 'click']) target.dispatchEvent(new MouseEvent(type, options))
    },
    clickWord(selector, word, detail = 1) {
        const { doc } = view.renderer.getContents()[0]
        const walker = doc.createTreeWalker(doc.querySelector(selector), NodeFilter.SHOW_TEXT)
        while (walker.nextNode()) {
            const index = walker.currentNode.data.indexOf(word)
            if (index < 0) continue
            const range = doc.createRange()
            range.setStart(walker.currentNode, index)
            range.setEnd(walker.currentNode, index + word.length)
            const rect = range.getClientRects()[0]
            this.clickAt(rect.left + rect.width / 2, rect.top + rect.height / 2, detail)
            return true
        }
        return false
    },
    markState: () => ({
        marking: marks?.marking ?? false,
        pending: marks?.pending ?? false,
        signs: marks?.signs ?? [],
        cursor: marks?.cursor ?? null,
        drawn: view.renderer.getContents()[0]?.overlayer?.element.childElementCount ?? 0,
    }),
    splitImages: () => {
        const { doc } = view.renderer.getContents()[0]
        const images = [...doc.querySelectorAll('img, svg')]
        return { images: images.length, split: images.filter(image => image.getClientRects().length > 1).length }
    },
    colorsOf: id => {
        const doc = view.renderer.getContents()[0].doc
        const element = doc.getElementById(id), style = getComputedStyle(element)
        return {
            color: style.color,
            background: style.backgroundColor,
            border: style.borderLeftColor,
            inline: element.getAttribute('style'),
        }
    },
    selectionAnchor: id => {
        const content = view.renderer.getContents()[0]
        const range = content.doc.createRange()
        range.selectNodeContents(content.doc.getElementById(id))
        return view.getCFI(content.index, range)
    },
    goToIndex: index => view.goTo(index),
    sections: () => view.book.sections.map(s => ({ linear: s.linear, size: s.size })),
    setAnswers: answers => fields?.setAnswers(answers),
    // For the tests: what a person does in a field, and what is in it.
    fieldInput(selector, value) {
        const element = view.renderer.getContents()[0].doc.querySelector(selector)
        if (element.type === 'checkbox') element.checked = value
        else element.value = value
        element.dispatchEvent(new Event(element.type === 'checkbox' ? 'change' : 'input', { bubbles: true }))
    },
    fieldState(selector) {
        const element = view.renderer.getContents()[0].doc.querySelector(selector)
        return {
            value: element.value,
            checked: element.checked,
            shown: element.getClientRects().length > 0,
            onPage: (() => {
                const frame = element.ownerDocument.defaultView.frameElement.getBoundingClientRect()
                const { left, right } = element.getBoundingClientRect()
                return left + frame.left >= 0 && right + frame.left <= window.innerWidth
            })(),
            windowHeight: window.innerHeight,
            height: element.offsetHeight,
            scroll: element.scrollHeight,
        }
    },
    // A key pressed in a field or, without selector, in the text. True if the app took it for itself.
    keyIn(selector, key) {
        const doc = view.renderer.getContents()[0].doc
        const target = selector ? doc.querySelector(selector) : doc.body
        const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })
        target.dispatchEvent(event)
        return event.defaultPrevented
    },
    fieldFocus(selector, on) {
        const element = view.renderer.getContents()[0].doc.querySelector(selector)
        element.dispatchEvent(new FocusEvent(on ? 'focusin' : 'focusout', { bubbles: true }))
    },
    peekState: () => peek?.state,
    peekHide: () => peek?.hide(),
    peekGo: () => peek?.clickGo(),
    // Links by their text; a real pointer on the screen does the same.
    linkEvent(text, type) {
        const doc = view.renderer.getContents()[0].doc
        const a = [...doc.querySelectorAll('a[href]')].find(link => link.textContent.trim() === text)
        if (!a) return false
        if (type === 'rect') {
            const frame = doc.defaultView.frameElement.getBoundingClientRect()
            const { left, right, top, bottom } = a.getClientRects()[0]
            return { left: left + frame.left, right: right + frame.left, top: top + frame.top, bottom: bottom + frame.top }
        }
        if (type === 'focusin') a.dispatchEvent(new FocusEvent('focusin', { bubbles: true }))
        else if (type === 'click') a.click()
        else a.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: doc.defaultView }))
        return true
    },
    isShown: id => {
        const element = view.renderer.getContents()[0].doc.getElementById(id)
        return element ? element.getClientRects().length > 0 : null
    },
    clickLink(index) {
        const doc = view.renderer.getContents()[0].doc
        doc.querySelectorAll('a[href]')[index]?.click()
    },
}
post('ready')
