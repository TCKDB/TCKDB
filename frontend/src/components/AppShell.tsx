import { Suspense, useEffect, useId, useRef, useState, type FocusEvent, type FormEvent, type KeyboardEvent } from "react"
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom"
import { LevelLabelScope } from "./LevelLabel"
import { ThemeToggle } from "./ThemeToggle"
import { AuthStatus } from "./AuthStatus"
import { Announcer } from "./Announcer"
import { LoadingPage } from "./LoadingPage"
import { RouteFocus } from "./RouteFocus"
import { RecordGlyph, type RecordKind } from "./RecordGlyph"
import { MAX_SEARCH_LENGTH } from "../domain/searchLimits"
import { PHONE_QUERY, useMediaQuery } from "../hooks/useMediaQuery"
import "../auth.css"
import "../app-shell.css"

type NavItem = { label: string; to: string; glyph?: RecordKind }

const MOLECULES: NavItem = { label: "Molecules", to: "/species", glyph: "species" }
const REACTIONS: NavItem = { label: "Reactions", to: "/reactions", glyph: "reaction" }
const METHODS: NavItem = { label: "Methods", to: "/methods" }
const GUIDE: NavItem = { label: "Guide", to: "/guide" }

const WIDE_LINKS = [MOLECULES, REACTIONS, METHODS, GUIDE]
const TAB_LINKS = [MOLECULES, REACTIONS, METHODS]

function NavItemLink({ item, className }: { item: NavItem; className?: string }) {
    return <NavLink className={className} to={item.to}>
        {item.glyph && <RecordGlyph kind={item.glyph} />}
        <span>{item.label}</span>
    </NavLink>
}

/** A search box that hands its text to the home page search. */
function SiteSearch() {
    const navigate = useNavigate()
    const [text, setText] = useState("")
    function submit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault()
        const trimmed = text.trim()
        if (trimmed) navigate(`/?q=${encodeURIComponent(trimmed)}`)
    }
    return <form className="site-search" role="search" onSubmit={submit}>
        <input
            type="search"
            maxLength={MAX_SEARCH_LENGTH}
            aria-label="Search molecules and reactions"
            placeholder="Search by name, formula, SMILES"
            autoComplete="off"
            value={text}
            onChange={(event) => setText(event.target.value)}
        />
    </form>
}

/** The fourth bottom-bar item: the pages that do not fit the bar, and the theme control. */
function MoreMenu() {
    const [open, setOpen] = useState(false)
    const buttonRef = useRef<HTMLButtonElement>(null)
    const menuRef = useRef<HTMLDivElement>(null)
    const menuId = useId()

    useEffect(() => {
        if (!open) return
        menuRef.current?.querySelector<HTMLElement>("a, button")?.focus()
        function onPointerDown(event: PointerEvent) {
            const target = event.target as Node
            if (!menuRef.current?.contains(target) && !buttonRef.current?.contains(target)) setOpen(false)
        }
        document.addEventListener("pointerdown", onPointerDown)
        return () => document.removeEventListener("pointerdown", onPointerDown)
    }, [open])

    function onKeyDown(event: KeyboardEvent<HTMLElement>) {
        if (event.key !== "Escape" || !open) return
        event.stopPropagation()
        setOpen(false)
        buttonRef.current?.focus()
    }

    function onBlur(event: FocusEvent<HTMLElement>) {
        if (!open) return
        const next = event.relatedTarget as Node | null
        if (!next || !event.currentTarget.contains(next)) setOpen(false)
    }

    return <div className="more-menu" onKeyDown={onKeyDown} onBlur={onBlur}>
        <button
            ref={buttonRef}
            type="button"
            className="tab-bar-item"
            aria-expanded={open}
            aria-controls={menuId}
            onClick={() => setOpen((wasOpen) => !wasOpen)}
        >
            <span>More</span>
        </button>
        <div ref={menuRef} id={menuId} className="more-menu-panel" role="group" aria-label="More" hidden={!open}>
            <NavLink to={GUIDE.to} onClick={() => setOpen(false)}>{GUIDE.label}</NavLink>
            <ThemeToggle />
        </div>
    </div>
}

export function AppShell() {
    const phone = useMediaQuery(PHONE_QUERY)
    const { pathname } = useLocation()
    return <div className="archive-shell">
        <a className="skip-link" href="#main-content">Skip to content</a>
        <header className="site-bar">
            <NavLink className="brand" to="/" aria-label="TCKDB home"><span>T</span> TCKDB</NavLink>
            {!phone && <nav className="site-nav" aria-label="Primary navigation">
                {WIDE_LINKS.map((item) => <NavItemLink key={item.to} item={item} />)}
            </nav>}
            <div className="site-bar-end">
                {!phone && <ThemeToggle />}
                <AuthStatus />
                <SiteSearch />
            </div>
        </header>
        <RouteFocus />
        <Announcer />
        <main id="main-content" tabIndex={-1}><LevelLabelScope key={pathname}><Suspense fallback={<LoadingPage />}><Outlet /></Suspense></LevelLabelScope></main>
        <footer className="archive-footer"><span>TCKDB · Theoretical Chemical Kinetics Database</span><span>Public scientific records</span></footer>
        {phone && <nav className="tab-bar" aria-label="Primary navigation">
            {TAB_LINKS.map((item) => <NavItemLink key={item.to} className="tab-bar-item" item={item} />)}
            <MoreMenu />
        </nav>}
    </div>
}
