import { useRouteError, isRouteErrorResponse, Link } from "react-router-dom";
import { errorState, pageWrapper, pageStack, pageTitle, caption, mono, linkSmall } from "@/theme/styles";

/**
 * Route-level error element — shown by react-router when a route
 * throws during render or data loading. Uses the design system
 * error state pattern.
 */
export function RouteError() {
  const error = useRouteError();

  let title = "Something went wrong";
  let detail = "An unexpected error occurred.";

  if (isRouteErrorResponse(error)) {
    title = `${error.status} ${error.statusText}`;
    detail = typeof error.data === "string" ? error.data : JSON.stringify(error.data);
  } else if (error instanceof Error) {
    detail = error.message;
  }

  return (
    <div className={`${pageWrapper} ${pageStack} mt-12`}>
      <h1 className={pageTitle}>{title}</h1>
      <div className={errorState}>
        <p className={`${mono} text-[12px] text-fail max-w-xl text-left whitespace-pre-wrap break-words`}>
          {detail}
        </p>
      </div>
      <div className={caption}>
        <Link to="/" className={linkSmall}>Return to dashboard</Link>
      </div>
    </div>
  );
}
