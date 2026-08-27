import { NavLink, Outlet } from "react-router-dom"

const links = [
    ["Species", "/species"],
    ["Reactions", "/reactions"],
    ["Methods", "/methods"],
] as const

export function AppShell() {
    return <div className="archive-shell">
        <a className="skip-link" href="#main-content">Skip to content</a>
        <header className="utility-bar">
            <NavLink className="brand" to="/" aria-label="TCKDB home"><span>T</span> TCKDB</NavLink>
            <nav aria-label="Primary navigation">
                {links.map(([label, path]) => <NavLink key={path} to={path}>{label}</NavLink>)}
            </nav>
        </header>
        <main id="main-content"><Outlet /></main>
        <footer className="archive-footer"><span>TCKDB · Theoretical Chemical Kinetics Database</span><span>Public scientific records</span></footer>
    </div>
}
