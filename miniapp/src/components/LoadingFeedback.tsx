import { Text, View } from '@tarojs/components'
import './loading-feedback.scss'

// Accepted preview C: Motion Primitives Text Shimmer Wave (MIT).
// CSS-only decoration: never controls request completion or business timing.
export default function LoadingFeedback ({ loading, hasContent = false, visible = true, text, refreshingText }: {
  loading: boolean, hasContent?: boolean, visible?: boolean, text: string, refreshingText?: string
}) {
  const label = hasContent ? refreshingText || '正在更新…' : text
  if (!hasContent && !loading) return null
  return <View className={`read-feedback ${hasContent ? 'read-feedback--compact' : 'read-feedback--initial'}`}>
    {loading && visible && <View className='read-wave' ariaRole='status' ariaLabel={label}>
      {Array.from(label).map((char, index, chars) => <Text className='read-wave-char' key={`${label}:${index}`}
        style={{ animationDelay: `${0.12 + index * 1.25 / chars.length}s` }}>{char}</Text>)}
    </View>}
  </View>
}
