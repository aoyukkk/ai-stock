<template>
  <main class="login-page">
    <section class="login-panel" aria-labelledby="login-title">
      <div class="brand-mark" aria-hidden="true">AI</div>
      <h1 id="login-title">{{ mustChange ? "设置新密码" : "合伙人共享登录" }}</h1>
      <p>{{ mustChange ? "首次登录后请设置至少 12 位的新密码。" : "使用项目共享账号进入交易工作台。" }}</p>

      <el-form v-if="!mustChange" label-position="top" @submit.prevent="login">
        <el-form-item label="共享账号">
          <el-input v-model.trim="username" autocomplete="username" maxlength="64" />
        </el-form-item>
        <el-form-item label="共享密码">
          <el-input v-model="currentPassword" type="password" show-password autocomplete="current-password" />
        </el-form-item>
        <el-button native-type="submit" type="primary" :loading="loading" :disabled="!username || !currentPassword">
          登录
        </el-button>
      </el-form>

      <el-form v-else label-position="top" @submit.prevent="changePassword">
        <el-form-item label="新密码">
          <el-input v-model="newPassword" type="password" show-password autocomplete="new-password" />
        </el-form-item>
        <el-form-item label="确认密码">
          <el-input v-model="confirmPassword" type="password" show-password autocomplete="new-password" />
        </el-form-item>
        <el-button native-type="submit" type="primary" :loading="loading">完成改密</el-button>
      </el-form>
    </section>
  </main>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";

import { internalAuthApi } from "@/api/internalAuth";
import { useInternalAuthStore } from "@/stores/internalAuth";

const router = useRouter();
const auth = useInternalAuthStore();
const username = ref("partners");
const currentPassword = ref("");
const newPassword = ref("");
const confirmPassword = ref("");
const mustChange = ref(false);
const loading = ref(false);

async function login() {
  loading.value = true;
  try {
    const result = await internalAuthApi.login(username.value, currentPassword.value);
    sessionStorage.setItem("ai-trader-csrf", result.data.csrf_token);
    if (result.data.must_change_password) {
      mustChange.value = true;
      return;
    }
    currentPassword.value = "";
    await finish();
  } catch {
    ElMessage.error("账号或密码错误，连续失败 5 次将锁定 15 分钟。");
  } finally {
    loading.value = false;
  }
}

async function changePassword() {
  if (newPassword.value.length < 12 || newPassword.value !== confirmPassword.value) {
    ElMessage.error("新密码至少 12 位，且两次输入必须一致。");
    return;
  }
  loading.value = true;
  try {
    await internalAuthApi.changePassword(currentPassword.value, newPassword.value);
    currentPassword.value = "";
    newPassword.value = "";
    confirmPassword.value = "";
    await finish();
  } catch {
    ElMessage.error("密码修改失败。");
  } finally {
    loading.value = false;
  }
}

async function finish() {
  auth.reset();
  await auth.initialize();
  await router.replace("/workbench");
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 20px;
  background: #eef2f5;
}
.login-panel {
  width: min(400px, 100%);
  padding: 32px;
  background: #fff;
  border: 1px solid #d9e0e6;
  border-radius: 8px;
  box-shadow: 0 12px 32px rgb(24 39 58 / 10%);
}
.brand-mark {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  margin: 0 auto 18px;
  border-radius: 8px;
  color: #fff;
  background: #173a63;
  font-weight: 700;
}
h1 { margin: 0; text-align: center; font-size: 22px; letter-spacing: 0; color: #18283b; }
p { margin: 10px 0 24px; text-align: center; color: #68788a; line-height: 1.6; }
.login-panel :deep(.el-button) { width: 100%; }
</style>
