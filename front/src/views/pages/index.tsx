import { lazy } from 'react'

const ErrorPage = lazy(() => import('./error/ErrorPage'))
const Home = lazy(() => import('./home/Home'))
const Changelog = lazy(() => import('./changelog/Changelog'))
const Glyphs = lazy(() => import('./glyphs/Glyphs'))
const Translation = lazy(() => import('./translation/Translation'))
const Cheats = lazy(() => import('./cheats/Cheats'))
const Voices = lazy(() => import('./voices/Voices'))
const About = lazy(() => import('./about/About'))

export { ErrorPage, Home, Changelog, Glyphs, Translation, Cheats, Voices, About }
