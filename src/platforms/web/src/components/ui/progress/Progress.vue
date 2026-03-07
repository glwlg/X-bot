<script setup lang="ts">
import { computed } from 'vue'

interface Props {
  modelValue?: number
  max?: number
  class?: string
}

const props = withDefaults(defineProps<Props>(), {
  modelValue: 0,
  max: 100,
  class: ''
})

const percentage = computed(() => {
  if (props.max === 0) return 0
  return Math.min(100, Math.max(0, (props.modelValue / props.max) * 100))
})
</script>

<template>
  <div
    :class="`relative h-4 w-full overflow-hidden rounded-full bg-secondary ${props.class}`"
  >
    <div
      class="h-full w-full flex-1 bg-primary transition-all duration-300 ease-in-out"
      :style="{ transform: `translateX(-${100 - percentage}%)` }"
    />
  </div>
</template>
