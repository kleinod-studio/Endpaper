// Searching the open book. foliate-js finds the places and outlines them; what the running text hides, such as
// footnotes shown in the popup, is left out, because a jump there would lead to nothing visible.
import { Overlayer } from './foliate-js/overlayer.js'
import { HIDDEN, hideNotes } from './peek.js'

const CURRENT = 'endpaper-search-current'
const COLOR = '#3584e4'
const PROGRESS_STEP = 0.05

export const createSearch = (view, post) => {
    let run = 0 // the search going on, numbered by reader_view.py; another number makes it stop
    let queue = Promise.resolve()
    let current = null
    const prepared = new WeakSet()

    const acceptNode = node => {
        if (node.nodeType !== Node.ELEMENT_NODE) return NodeFilter.FILTER_ACCEPT
        const doc = node.ownerDocument
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

    const search = async (query, mine) => {
        if (mine !== run) return
        clearCurrent()
        view.clearSearch()
        if (!query) return
        let count = 0
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
                    count += result.subitems.length
                    post('search-results', {
                        run: mine,
                        chapter: result.label.trim(),
                        items: result.subitems.map(({ cfi, excerpt }) => ({ cfi, ...excerpt })),
                    })
                } else if (result.progress >= shown + PROGRESS_STEP) {
                    shown = result.progress
                    post('search-progress', { run: mine, progress: result.progress })
                }
            }
            post('search-done', { run: mine, count })
        } catch (error) {
            post('search-done', { run: mine, count, failed: String(error) })
        }
    }

    return {
        start(query, mine) {
            run = mine
            queue = queue.then(() => search(query, mine))
            return queue
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
