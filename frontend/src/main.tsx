import React from 'react'
import ReactDOM from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'

import { App } from './App'
import './index.css'
import { CampaignPage } from './pages/Campaign'
import { Ingest } from './pages/Ingest'
import { JobProgress } from './pages/JobProgress'
import { Posts } from './pages/Posts'
import { Review } from './pages/Review'
import { Settings } from './pages/Settings'

const router = createBrowserRouter([
  {
    path: '/',
    element: <App />,
    children: [
      { index: true, element: <Ingest /> },
      { path: 'campaigns', element: <CampaignPage /> },
      { path: 'campaigns/:campaignId', element: <CampaignPage /> },
      { path: 'jobs/:jobId', element: <JobProgress /> },
      { path: 'jobs/:jobId/clips', element: <Review /> },
      { path: 'posts', element: <Posts /> },
      { path: 'settings', element: <Settings /> },
    ],
  },
])

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
)
