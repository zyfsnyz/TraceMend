import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AppLayout } from './components/AppLayout';
import { OverviewPage } from './pages/OverviewPage';
import { TracesPage } from './pages/TracesPage';
import { TraceDetailPage } from './pages/TraceDetailPage';
import { RisksPage } from './pages/RisksPage';
import { GuidePage } from './pages/GuidePage';

export default function App() {
  return <BrowserRouter><Routes><Route element={<AppLayout />}>
    <Route index element={<OverviewPage />} />
    <Route path="traces" element={<TracesPage />} />
    <Route path="traces/:traceId" element={<TraceDetailPage />} />
    <Route path="risks" element={<RisksPage />} />
    <Route path="guide" element={<GuidePage />} />
    <Route path="*" element={<Navigate to="/" replace />} />
  </Route></Routes></BrowserRouter>;
}
