// Makes a book readable on dark and sepia pages, whatever colors its own style sheet insists on.
// Style sheets cannot do this: books such as those from jw.org carry thousands of !important rules.
// So this reads the real colors of each element and adjusts only what needs it, keeping the hue.
// Everything is written as inline styles and can be undone. Inline styles do not affect anchors (CFIs).

const TEXT_CONTRAST = 5
const NEUTRAL_TEXT_CONTRAST = 7
const BORDER_CONTRAST = 2.5
const NEUTRAL_SATURATION = 0.18
const SKIP = new Set(['IMG', 'SVG', 'svg', 'VIDEO', 'CANVAS', 'PICTURE', 'STYLE', 'SCRIPT', 'HEAD', 'TITLE'])
const BORDERS = ['top', 'right', 'bottom', 'left']

const parseColor = text => {
    const match = /^rgba?\(([^)]+)\)$/.exec(text)
    if (!match) return null
    const [r, g, b, a = 1] = match[1].split(/[\s,/]+/).map(Number)
    return [r, g, b].some(Number.isNaN) ? null : { r, g, b, a: Number.isNaN(a) ? 1 : a }
}
const toHex = ({ r, g, b }) => `rgb(${Math.round(r)}, ${Math.round(g)}, ${Math.round(b)})`

const over = (top, bottom) => ({
    r: top.r * top.a + bottom.r * (1 - top.a),
    g: top.g * top.a + bottom.g * (1 - top.a),
    b: top.b * top.a + bottom.b * (1 - top.a),
    a: 1,
})

const luminance = ({ r, g, b }) => {
    const channel = v => {
        const s = v / 255
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
    }
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
}

export const contrast = (a, b) => {
    const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x)
    return (high + 0.05) / (low + 0.05)
}

const toHsl = ({ r, g, b }) => {
    const [R, G, B] = [r / 255, g / 255, b / 255]
    const max = Math.max(R, G, B), min = Math.min(R, G, B), d = max - min
    const l = (max + min) / 2
    if (!d) return { h: 0, s: 0, l }
    const s = d / (1 - Math.abs(2 * l - 1))
    const h = max === R ? ((G - B) / d + 6) % 6 : max === G ? (B - R) / d + 2 : (R - G) / d + 4
    return { h: h * 60, s, l }
}

const fromHsl = ({ h, s, l }) => {
    const c = (1 - Math.abs(2 * l - 1)) * s
    const x = c * (1 - Math.abs((h / 60) % 2 - 1))
    const m = l - c / 2
    const [r, g, b] = [[c, x, 0], [x, c, 0], [0, c, x], [0, x, c], [x, 0, c], [c, 0, x]][Math.floor(h / 60) % 6]
    return { r: (r + m) * 255, g: (g + m) * 255, b: (b + m) * 255, a: 1 }
}

// The nearest lightness of this hue that reaches the contrast against the background.
const search = (hsl, s, background, wanted, lighten) => {
    let [low, high] = lighten ? [hsl.l, 1] : [0, hsl.l]
    for (let i = 0; i < 14; i++) {
        const middle = (low + high) / 2
        const enough = contrast(fromHsl({ h: hsl.h, s, l: middle }), background) >= wanted
        if (lighten === enough) high = middle
        else low = middle
    }
    return fromHsl({ h: hsl.h, s, l: lighten ? high : low })
}

const liftTo = (color, background, wanted) => {
    const hsl = toHsl(color)
    const s = Math.min(hsl.s, 0.8)
    const lighten = luminance(background) < 0.4
    const first = search(hsl, s, background, wanted, lighten)
    if (contrast(first, background) >= wanted) return first
    // On a middling background one direction may not get there; then the better of the two.
    const second = search(hsl, s, background, wanted, !lighten)
    return contrast(second, background) > contrast(first, background) ? second : first
}

const WHITE = { r: 255, g: 255, b: 255, a: 1 }
const INK = { r: 17, g: 17, b: 20, a: 1 }

const adaptText = (color, background, theme) => {
    const solid = over(color, background)
    const before = contrast(solid, background)
    const { s } = toHsl(solid)
    let result = null
    if (s < NEUTRAL_SATURATION) {
        if (before >= NEUTRAL_TEXT_CONTRAST) return null
        // Plain text takes the theme's color; on a colored box, whichever of white and ink reads best.
        result = contrast(theme.fgColor, background) >= NEUTRAL_TEXT_CONTRAST ? theme.fgColor
            : contrast(WHITE, background) >= contrast(INK, background) ? WHITE : INK
    } else if (before < TEXT_CONTRAST) {
        result = liftTo(solid, background, TEXT_CONTRAST)
    }
    // A change may only ever make the text easier to read.
    return result && contrast(result, background) > before ? result : null
}

// Returns the new background: null to leave it, 'none' to remove it, otherwise a color.
const adaptBackground = (color, theme) => {
    if (color.a < 0.05) return null
    const { s, l, h } = toHsl(over(color, theme.bgColor))
    if (s < NEUTRAL_SATURATION) {
        if (l >= 0.97) return 'none' // white paper
        if (theme.dark ? l > 0.5 : l > 0.75) return theme.surfaceColor // light boxes stay boxes
        return null
    }
    if (theme.dark && l > 0.45) return fromHsl({ h, s: s * 0.55, l: 0.24 })
    return null
}

const undoList = new WeakMap()

const write = (list, element, property, value) => {
    const { style } = element
    list.push([element, property, style.getPropertyValue(property), style.getPropertyPriority(property)])
    style.setProperty(property, value, 'important')
}

export const undo = doc => {
    for (const [element, property, value, priority] of undoList.get(doc) ?? []) {
        if (value) element.style.setProperty(property, value, priority)
        else element.style.removeProperty(property)
    }
    undoList.delete(doc)
}

export const adapt = (doc, theme) => {
    undo(doc)
    if (!theme.adapt || !doc.body) return
    theme = {
        ...theme,
        bgColor: parseColor(theme.bgRgb),
        fgColor: parseColor(theme.fgRgb),
        surfaceColor: parseColor(theme.surfaceRgb),
    }
    const view = doc.defaultView
    const changes = []
    const memo = new Map()
    const remembered = (key, make) => {
        if (!memo.has(key)) memo.set(key, make())
        return memo.get(key)
    }

    // First read everything, then write: reading after writing would recalculate styles every time.
    // A box with a picture behind it keeps the colors of the book, because the picture stays as it is. Its text is
    // read against the color the book gives for where the picture does not reach, which matches the picture.
    const visit = (element, background, onPicture) => {
        if (SKIP.has(element.tagName.toUpperCase())) return // in a book's XHTML, names are in lower case
        const style = view.getComputedStyle(element)
        let here = background
        if (style.backgroundImage !== 'none') {
            const color = parseColor(style.backgroundColor)
            if (color && color.a >= 0.5) {
                here = over(color, background)
                onPicture = true
            }
        } else if (onPicture) {
            const color = parseColor(style.backgroundColor)
            if (color && color.a >= 0.05) here = over(color, background)
        } else {
            const color = parseColor(style.backgroundColor)
            if (color) {
                const mapped = remembered(`b${style.backgroundColor}`, () => adaptBackground(color, theme))
                if (mapped === 'none') changes.push([element, 'background-color', 'transparent'])
                else if (mapped) {
                    changes.push([element, 'background-color', toHex(mapped)])
                    here = mapped
                } else if (color.a >= 0.05) here = over(color, background)
            }
        }
        const color = parseColor(style.color)
        if (color) {
            const text = remembered(`t${style.color}|${toHex(here)}`, () => adaptText(color, here, theme))
            if (text) changes.push([element, 'color', toHex(text)])
        }
        for (const side of BORDERS) {
            if (style.getPropertyValue(`border-${side}-style`) === 'none'
                || style.getPropertyValue(`border-${side}-width`) === '0px') continue
            const value = style.getPropertyValue(`border-${side}-color`)
            const border = parseColor(value)
            if (!border || border.a < 0.05) continue
            const solid = over(border, here)
            if (contrast(solid, here) < BORDER_CONTRAST) {
                changes.push([element, `border-${side}-color`, toHex(remembered(
                    `s${value}|${toHex(here)}`, () => liftTo(solid, here, BORDER_CONTRAST)))])
            }
        }
        for (const child of element.children) visit(child, here, onPicture)
    }
    for (const child of doc.body.children) visit(child, theme.bgColor, false)

    const list = []
    for (const [element, property, value] of changes) write(list, element, property, value)
    undoList.set(doc, list)
}

// For the tests: how readable is the text really? Looks at elements that hold text themselves.
export const audit = (doc, pageBackground) => {
    const view = doc.defaultView
    const page = parseColor(pageBackground)
    let checked = 0, below = 0, lowest = 21, sample = null, detail = null
    const visit = (element, background) => {
        if (SKIP.has(element.tagName.toUpperCase())) return
        const style = view.getComputedStyle(element)
        let here = background
        const color = parseColor(style.backgroundColor)
        if (color && color.a >= (style.backgroundImage === 'none' ? 0.05 : 0.5)) here = over(color, background)
        const hasText = [...element.childNodes].some(n => n.nodeType === 3 && n.nodeValue.trim())
        const text = parseColor(style.color)
        if (hasText && text && style.display !== 'none' && style.visibility !== 'hidden') {
            const ratio = contrast(over(text, here), here)
            checked++
            if (ratio < lowest) {
                lowest = ratio
                sample = element.textContent.trim().slice(0, 30)
                detail = `${style.color} on ${toHex(here)} <${element.tagName} ${element.className}>`
            }
            if (ratio < 4.5) below++
        }
        for (const child of element.children) visit(child, here)
    }
    if (doc.body) visit(doc.body, page)
    return { checked, below, lowest: Math.round(lowest * 10) / 10, sample, detail }
}
