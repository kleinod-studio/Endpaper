// A picture in the book, shown large in the app after a click on it, or Enter once it has the focus.
// Books are not trusted: the picture is drawn anew here, in the web process, and handed on as a plain PNG,
// so that nothing from the book has to be decoded outside of it.

const XLINK = 'http://www.w3.org/1999/xlink'
const MIN_SIDE = 48 // smaller pictures are icons and ornaments, nothing to look at
const MAX_SIDE = 4096

const sourceOf = element => element.localName === 'img'
    ? element.currentSrc || element.src
    : element.href?.baseVal || element.getAttributeNS(XLINK, 'href') || element.getAttribute('href')

const isLarge = element => {
    const { width, height } = element.getBoundingClientRect()
    return width >= MIN_SIDE && height >= MIN_SIDE
}

// A picture inside a link belongs to the link.
const pictureAt = target => {
    const element = target?.closest?.('img, image')
    return element && !element.closest('a[href]') && isLarge(element) ? element : null
}

const describe = element =>
    (element.getAttribute('alt') ?? element.closest('svg')?.querySelector('title')?.textContent ?? '').trim()

const asPng = async element => {
    const image = new Image()
    image.src = sourceOf(element)
    await image.decode()
    const shown = element.getBoundingClientRect()
    // An SVG may have no size of its own; then twice its size on the page keeps it sharp.
    let width = image.naturalWidth || shown.width * 2
    let height = image.naturalHeight || shown.height * 2
    const scale = Math.min(1, MAX_SIDE / Math.max(width, height))
    width = Math.max(1, Math.round(width * scale))
    height = Math.max(1, Math.round(height * scale))
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    canvas.getContext('2d').drawImage(image, 0, 0, width, height)
    const blob = await new Promise(done => canvas.toBlob(done, 'image/png'))
    return new Promise((done, fail) => {
        const reader = new FileReader()
        reader.onload = () => done(reader.result)
        reader.onerror = () => fail(reader.error)
        reader.readAsDataURL(blob)
    })
}

export const createPictures = (view, post, { isBusy }) => {
    const show = async element => {
        try {
            post('picture', { data: await asPng(element), description: describe(element) })
        } catch {
            post('picture-failed')
        }
    }

    return {
        attach(doc) {
            // Reached with the Tab key too, once it is laid out and known to be large enough.
            for (const element of doc.querySelectorAll('img')) {
                element.decode?.().then(() => requestAnimationFrame(() => {
                    if (pictureAt(element) === element) element.tabIndex = 0
                })).catch(() => {})
            }
            doc.addEventListener('click', e => {
                if (e.button !== 0 || e.defaultPrevented || isBusy()) return
                const element = pictureAt(e.target)
                if (!element) return
                e.preventDefault()
                show(element)
            })
            doc.addEventListener('keydown', e => {
                if (e.key !== 'Enter' || isBusy() || pictureAt(e.target) !== e.target) return
                e.preventDefault()
                show(e.target)
            })
        },
        show,
    }
}
