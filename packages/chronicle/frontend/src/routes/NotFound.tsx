import { Link } from "react-router-dom";
import { pageStack, pageWrapper, pageTitle, linkSmall, emptyState, caption } from "@/theme/styles";

export function NotFound() {
  return (
    <div className={`${pageWrapper} ${pageStack}`}>
      <h2 className={pageTitle}>Page not found</h2>
      <div className={emptyState}>
        <p className={caption}>The page you requested does not exist.</p>
        <Link to="/runs" className={`mt-2 ${linkSmall}`}>&larr; Go to Runs</Link>
      </div>
    </div>
  );
}
