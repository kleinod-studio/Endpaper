// The reading area. It runs inside the web view and talks to reader_view.py through messages.
// Books are not trusted: the Content Security Policy set by reader_view.py keeps their scripts from running.
import './foliate-js/view.js'
import { adapt, audit } from './contrast.js'

const post = (type, data = {}) =>
    window.webkit.messageHandlers.endpaper.postMessage(JSON.stringify({ type, ...data }))

const THEMES = {
    light: { bg: '#ffffff', fg: '#2e2e32', scheme: 'light', adapt: false },
    sepia: {
        bg: '#f5eedc', fg: '#40342a', scheme: 'light', adapt: true, dark: false,
        bgRgb: 'rgb(245, 238, 220)', fgRgb: 'rgb(64, 52, 42)', surfaceRgb: 'rgb(233, 224, 200)',
    },
    dark: {
        bg: '#222226', fg: '#e3e3e6', scheme: 'dark', adapt: true, dark: true,
        bgRgb: 'rgb(34, 34, 38)', fgRgb: 'rgb(227, 227, 230)', surfaceRgb: 'rgb(50, 50, 56)',
    },
}
const LINE_HEIGHT = { compact: 1.3, normal: 1.5, relaxed: 1.8 }
const TEXT_WIDTH = { narrow: 520, normal: 640, wide: 800 }
const FONT = { serif: 'serif', 'sans-serif': 'sans-serif' }

let view = null
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
    /* Answer fields in study editions: what is typed there would be lost when the book closes. */
    textarea, input, select, button { display: none !important; }
    `
}

const applyLayout = () => {
    const theme = THEMES[settings.theme]
    document.documentElement.style.background = theme.bg
    document.body.style.background = theme.bg
    if (!view?.renderer) return
    const r = view.renderer
    r.setAttribute('flow', settings.flow)
    r.setAttribute('margin', '48px')
    r.setAttribute('gap', '7%')
    r.setAttribute('max-inline-size', `${TEXT_WIDTH[settings.width]}px`)
    r.setAttribute('max-block-size', '100000px')
    r.setAttribute('max-column-count', '1')
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
    if (e.target?.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target?.tagName ?? '')) return
    const paged = settings.flow === 'paginated'
    switch (e.key) {
        case 'ArrowRight': case 'PageDown': view?.goRight(); break
        case 'ArrowLeft': case 'PageUp': view?.goLeft(); break
        case 'ArrowDown': if (!paged) return; view?.next(); break
        case 'ArrowUp': if (!paged) return; view?.prev(); break
        case ' ': if (e.shiftKey) view?.prev(); else view?.next(); break
        case 'Home': view?.goTo(0); break
        case 'BackSpace': view?.history.back(); break
        default: return
    }
    e.preventDefault()
}
document.addEventListener('keydown', onKeydown)

let wheelSum = 0
let wheelLockedUntil = 0
const onWheel = e => {
    if (settings.flow !== 'paginated' || e.ctrlKey) return
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

window.endpaper = {
    async open(url, name, nextSettings, position) {
        settings = nextSettings
        applyLayout()
        try {
            const blob = await (await fetch(url)).blob()
            view = document.createElement('foliate-view')
            view.addEventListener('relocate', ({ detail }) => post('relocate', {
                cfi: detail.cfi ?? null,
                fraction: Number.isFinite(detail.fraction) ? detail.fraction : 0,
                tocLabel: detail.tocItem?.label?.trim() ?? '',
                tocHref: detail.tocItem?.href ?? '',
                canGoBack: view.history.canGoBack,
            }))
            view.addEventListener('load', ({ detail: { doc } }) => {
                doc.addEventListener('securitypolicyviolation', onViolation)
                doc.addEventListener('keydown', onKeydown)
                doc.addEventListener('wheel', onWheel, { passive: false })
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
    clickLink(index) {
        const doc = view.renderer.getContents()[0].doc
        doc.querySelectorAll('a[href]')[index]?.click()
    },
}
post('ready')
