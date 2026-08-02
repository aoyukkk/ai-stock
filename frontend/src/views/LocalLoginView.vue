<template>
  <main class="login-page">
    <section class="login-panel" aria-labelledby="login-title">
      <div class="brand-mark" aria-hidden="true">AI</div>
      <h1 id="login-title">内部交易助手</h1>
      <p>请输入共享密码以进入工作台。</p>
      <el-form label-position="top" @submit.prevent="login">
        <el-form-item label="共享密码">
          <el-input v-model="password" type="password" show-password autocomplete="current-password" />
        </el-form-item>
        <el-button native-type="submit" type="primary" :loading="loading" :disabled="!password">登录</el-button>
      </el-form>
    </section>
  </main>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";

import { internalAuthApi } from "@/api/internalAuth";
import { useInternalAuthStore } from "@/stores/internalAuth";

const router = useRouter();
const auth = useInternalAuthStore();
const password = ref("");
const loading = ref(false);

onMounted(async () => {
  try {
    await auth.initialize();
    await router.replace("/workbench");
  } catch {
    // Unauthenticated is the expected state for this public login shell.
  }
});

async function login() {
  loading.value = true;
  try {
    const result = await internalAuthApi.login(password.value);
    sessionStorage.setItem("ai-trader-csrf", result.data.csrf_token);
    password.value = "";
    auth.reset();
    await auth.initialize();
    await router.replace("/workbench");
  } catch {
    ElMessage.error("密码错误或账户暂时不可用。请稍后重试。");
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.login-page { min-height: 100vh; display: grid; place-items: center; padding: 20px; background: #eef2f5; }
.login-panel { width: min(400px, 100%); padding: 32px; background: #fff; border: 1px solid #d9e0e6; border-radius: 8px; box-shadow: 0 12px 32px rgb(24 39 58 / 10%); }
.brand-mark { display: grid; place-items: center; width: 44px; height: 44px; margin: 0 auto 18px; border-radius: 8px; color: #fff; background: #173a63; font-weight: 700; }
h1 { margin: 0; text-align: center; font-size: 22px; color: #18283b; }
p { margin: 10px 0 24px; text-align: center; color: #68788a; line-height: 1.6; }
.login-panel :deep(.el-button) { width: 100%; }
</style>
