import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { internalAuthApi, type InternalIdentity, type InternalRole } from "@/api/internalAuth";

export const useInternalAuthStore = defineStore("internal-auth", () => {
  const identity = ref<InternalIdentity | null>(null);
  const initialized = ref(false);
  const role = computed<InternalRole>(() => window.aiTraderShell ? "ADMIN" : (identity.value?.role || "VIEWER"));
  const canWrite = computed(() => role.value === "ADMIN" || role.value === "TRADER");
  const isAdmin = computed(() => role.value === "ADMIN");

  async function initialize() {
    if (initialized.value || window.aiTraderShell) {
      initialized.value = true;
      return;
    }
    identity.value = (await internalAuthApi.me()).data;
    initialized.value = true;
  }

  function reset() {
    identity.value = null;
    initialized.value = false;
  }

  return { identity, initialized, role, canWrite, isAdmin, initialize, reset };
});
