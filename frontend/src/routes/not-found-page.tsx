import { Link } from 'react-router-dom'

export function NotFoundPage() {
  return (
    <div className="flex min-h-screen items-center justify-center px-6 text-foreground">
      <div className="space-y-4 text-center">
        <p className="text-sm uppercase tracking-[0.3em] text-muted-foreground">
          404
        </p>
        <h1 className="text-4xl font-semibold">Page not found</h1>
        <Link
          to="/"
          className="inline-flex h-10 items-center justify-center rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90"
        >
          Back home
        </Link>
      </div>
    </div>
  )
}
