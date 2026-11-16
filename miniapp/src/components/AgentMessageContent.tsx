import { memo } from 'react'
import { Text, View } from '@tarojs/components'
import { parseAgentMessage } from '../core/agent-message-format'
import './AgentMessageContent.scss'

// Memoization avoids reparsing unchanged history on input and polling updates.
// Text nodes are intentional: model text cannot create HTML, links or buttons.
export default memo(function AgentMessageContent({ content }: { content: string }) {
  return <View className='agent-message-content'>
    {parseAgentMessage(content).map((block, index) => <View
      className={`agent-text-block agent-text-${block.kind}`}
      key={index}
    >
      {block.marker && <Text className='agent-list-marker'>{block.marker}</Text>}
      <Text className='agent-block-text' selectable>
        {block.spans.map((span, spanIndex) => <Text
          key={spanIndex}
          className={`${span.bold ? 'agent-text-bold' : ''} ${span.code ? 'agent-text-mono' : ''}`}
        >{span.text}</Text>)}
      </Text>
    </View>)}
  </View>
})
