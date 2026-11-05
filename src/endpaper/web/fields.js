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

export const createFields = (view, post) => {
    let answers = {}
    let checked = false
    let followTimer = 0
    const fieldsIn = new WeakMap() // must not keep a chapter in memory after the reader has left it

    const show = ({ element, key }) => {
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
    }

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
        attach(doc, index) {
            const section = view.book.sections[index]?.id ?? String(index)
            const items = fieldsOf(doc, section)
            fieldsIn.set(doc, items)
            for (const item of items) {
                show(item)
                item.element.addEventListener(isCheck(item.element) ? 'change' : 'input', () => {
                    grow(item.element)
                    send(item)
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
