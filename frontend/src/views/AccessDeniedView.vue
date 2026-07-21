<template>
  <main class="access-state">
    <el-result :icon="forbidden ? 'warning' : 'error'" :title="forbidden ? '权限不足' : '登录已失效'" :sub-title="forbidden ? '当前角色不能执行此操作。' : '请重新通过 Cloudflare Access 登录。'">
      <template #extra><el-button type="primary" @click="returnToApp">返回工作台</el-button></template>
    </el-result>
  </main>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
const route = useRoute(); const router = useRouter();
const forbidden = computed(() => route.path === "/forbidden");
function returnToApp() { void router.replace("/workbench"); }
</script>

<style scoped>.access-state { min-height:100vh; display:grid; place-items:center; background:#f4f7f9; }</style>
