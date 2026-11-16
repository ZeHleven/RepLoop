// babel-preset-taro 更多选项和默认值：
// https://docs.taro.zone/docs/next/babel-config
// The WeChat preview parser can lag behind modern browserslist defaults.
// Lower syntax in our build instead of relying on developer-tool ES6 switches.
const weappSyntaxOptions = process.env.TARO_ENV === 'weapp'
  ? { targets: { chrome: '64', ios: '12' }, ignoreBrowserslistConfig: true }
  : {}

module.exports = {
  presets: [
    ['taro', {
      framework: 'react',
      ts: true,
      compiler: 'webpack5',
      ...weappSyntaxOptions,
    }]
  ]
}
