import { createHashRouter, Navigate } from 'react-router-dom'
import { LazyPage, RootLayout } from './Layout'

import {
  ErrorPage,
  Home,
  Changelog,
  Glyphs,
  Translation,
  Cheats,
  Voices,
  About
} from '@/views/pages'

export const router = createHashRouter([
  {
    path: '/',
    element: <RootLayout />,
    errorElement: <ErrorPage />,
    children: [
      {
        index: true,
        element: (
          <LazyPage>
            <Home />
          </LazyPage>
        )
      },
      {
        path: 'translation',
        element: (
          <LazyPage>
            <Translation />
          </LazyPage>
        )
      },
      {
        path: 'glyphs',
        element: (
          <LazyPage>
            <Glyphs />
          </LazyPage>
        )
      },
      {
        path: 'cheats',
        element: (
          <LazyPage>
            <Cheats />
          </LazyPage>
        )
      },
      {
        path: 'voices',
        element: (
          <LazyPage>
            <Voices />
          </LazyPage>
        )
      },
      {
        path: 'about',
        element: (
          <LazyPage>
            <About />
          </LazyPage>
        )
      },
      {
        path: 'changelog',
        element: (
          <LazyPage>
            <Changelog />
          </LazyPage>
        )
      },
      {
        path: '*',
        element: <Navigate to="/" replace />
      }
    ]
  }
])
