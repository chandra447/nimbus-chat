import { createBrowserRouter } from 'react-router-dom'

import { HomePage } from '@/routes/home-page'
import { NotFoundPage } from '@/routes/not-found-page'

export const router = createBrowserRouter([
  {
    path: '/',
    element: <HomePage />,
    errorElement: <NotFoundPage />,
  },
])
