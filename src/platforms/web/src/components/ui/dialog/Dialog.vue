<script setup lang="ts">
import { DialogRoot, type DialogRootEmits, type DialogRootProps } from 'radix-vue'

const props = defineProps<DialogRootProps>()
const emits = defineEmits<DialogRootEmits>()
</script>

<template>
  <DialogRoot 
    :open="props.open"
    :default-open="props.defaultOpen"
    :modal="props.modal"
    @update:open="emits('update:open', $event)"
  >
    <slot />
  </DialogRoot>
</template>
