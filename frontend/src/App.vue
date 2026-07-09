<template>
  <el-config-provider :locale="elementLocale">
    <RouterView />
  </el-config-provider>
</template>

<script setup lang="ts">
import en from "element-plus/es/locale/lang/en";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import { computed } from "vue";

import { useLanguageStore } from "@/stores/languageStore";

const language = useLanguageStore();
const elementLocale = computed(() => (language.current === "zh-CN" ? zhCn : en));
</script>
