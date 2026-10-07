import { lazy, Suspense } from 'react';
import { createRoot } from 'react-dom/client';
const player = location.pathname === '/player' || location.pathname === '/obs';
const Page = lazy(() => player ? import('./player/Player').then(module => ({ default: module.Player })) : import('./editor/App').then(module => ({ default: module.App })));
createRoot(document.getElementById('root')!).render(<Suspense fallback={null}><Page /></Suspense>);
