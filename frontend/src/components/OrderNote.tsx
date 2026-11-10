import { Link } from "react-router-dom"
import "../record-blocks.css"

/** States how a list is ordered. The default is deposit order; a list with another order passes its own wording. */
export function OrderNote({ order = "Listed in deposit order." }: { order?: string }) {
    return (
        <p className="order-note">
            {order} This order is not a recommendation. <Link to="/guide#order">How lists are ordered</Link>
        </p>
    )
}
