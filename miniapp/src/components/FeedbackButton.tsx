import { Button, View, type ButtonProps } from '@tarojs/components'
import './micro-interactions.scss'

interface Props extends ButtonProps {
  busy?: boolean
  busyLabel?: string
}

export default function FeedbackButton ({
  busy = false, busyLabel = '保存中', disabled = false, children, className = '', onClick, ...props
}: Props) {
  const unavailable = Boolean(disabled || busy)
  return (
    <Button
      {...props}
      className={`micro-button ${className}`}
      disabled={unavailable}
      hoverClass={unavailable ? 'none' : 'micro-button-pressed'}
      hoverStartTime={0}
      hoverStayTime={60}
      onClick={event => { if (!unavailable) return onClick?.(event) }}
    >
      <View className='micro-button-content'>{busy ? busyLabel : children}</View>
    </Button>
  )
}
