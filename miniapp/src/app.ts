import { createElement, Fragment, PropsWithChildren } from 'react'
import { useDidHide, useLaunch } from '@tarojs/taro'
import { capsuleNavigation } from './core/capsule-platform'

import { logMiniappBuildDiagnostic } from './core/build-info'
import { initializeCloudTransport } from './core/request'
import './app.scss'

// Keep the native custom-tab-bar entry OUT of the WeChat App dependency graph.
// A static H5 import registers Component during App startup and consumes its
// webpack module before WeChat loads the real custom-tab-bar host.
const H5CapsuleHost = process.env.TARO_ENV === 'h5'
  ? require('./components/H5CapsuleHost').default
  : null

function App({ children }: PropsWithChildren<any>) {
  useDidHide(() => capsuleNavigation.pause())
  useLaunch(() => {
    logMiniappBuildDiagnostic()
    initializeCloudTransport()
  })

  return process.env.TARO_ENV === 'h5' ? createElement(Fragment,null,children,createElement(H5CapsuleHost)) : children
}

export default App
