import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getBooks, type Book } from '@/api/accounting'

export const useAccountingStore = defineStore('accounting', () => {
    const books = ref<Book[]>([])
    const currentBookId = ref<number | null>(null)
    const loading = ref(false)

    async function fetchBooks() {
        loading.value = true
        try {
            const res = await getBooks()
            books.value = res.data
            if (books.value.length > 0 && !currentBookId.value) {
                const first = books.value[0]
                if (first) currentBookId.value = first.id
            }
        } finally {
            loading.value = false
        }
    }

    function setCurrentBook(id: number) {
        currentBookId.value = id
    }

    return { books, currentBookId, loading, fetchBooks, setCurrentBook }
})
