const CSRF_COOKIE_NAME = process.env.NEXT_PUBLIC_CSRF_COOKIE_NAME ?? "biblio_csrf_token";

export function getCsrfToken(): string | null {
  if (typeof window === "undefined") return null;
  const cookies = document.cookie.split(";").map((cookie) => cookie.trim());
  const csrfCookie = cookies.find((cookie) => cookie.startsWith(`${CSRF_COOKIE_NAME}=`));
  if (!csrfCookie) return null;
  return decodeURIComponent(csrfCookie.slice(CSRF_COOKIE_NAME.length + 1));
}
