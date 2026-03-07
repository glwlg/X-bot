<script setup lang="ts">
import { TooltipContent, type TooltipContentProps, TooltipPortal, useForwardProps } from 'radix-vue'
import { cn } from '@/lib/utils'

const props = withDefaults(defineProps<TooltipContentProps>(), {
  sideOffset: 4,
})

const forwarded = useForwardProps(props)
</script>

<template>
  <TooltipPortal>
    <TooltipContent
      v-bind="forwarded"
      :class="cn('z-50 overflow-hidden rounded-md bg-white border border-slate-200 px-3 py-1.5 text-xs text-slate-950 shadow-md animate-in fade-in-0 zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 dark:bg-slate-950 dark:text-slate-50 dark:border-slate-800', $attrs.class ?? '')"
    >
      <slot />
    </TooltipContent>
  </TooltipPortal>
</template>
