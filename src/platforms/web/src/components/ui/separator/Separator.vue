<script setup lang="ts">
import { computed } from 'vue'

interface Props {
  orientation?: 'horizontal' | 'vertical'
  decorative?: boolean
  class?: string
}

const props = withDefaults(defineProps<Props>(), {
  orientation: 'horizontal',
  decorative: true,
  class: ''
})

const separatorClasses = computed(() => {
  const baseClasses = 'shrink-0 bg-border'
  
  const orientationClasses = {
    horizontal: 'h-[1px] w-full',
    vertical: 'h-full w-[1px]'
  }
  
  return `${baseClasses} ${orientationClasses[props.orientation]} ${props.class}`
})
</script>

<template>
  <div
    :class="separatorClasses"
    :role="decorative ? 'none' : 'separator'"
    :aria-orientation="decorative ? undefined : orientation"
  />
</template>
