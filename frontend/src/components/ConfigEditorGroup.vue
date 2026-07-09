<template>
  <el-card class="config-editor-group" shadow="never">
    <template #header>
      <div class="config-editor-group__header">
        <span>{{ title }}</span>
        <el-tag v-if="dirty" type="warning" effect="plain">{{ language.t("common.modified") }}</el-tag>
      </div>
    </template>

    <el-alert
      v-if="danger"
      class="config-editor-group__alert"
      type="error"
      :closable="false"
      show-icon
      :title="language.t('config.riskAlert')"
    />
    <el-alert
      v-if="validationMessage"
      class="config-editor-group__alert"
      type="warning"
      :closable="false"
      show-icon
      :title="validationMessage"
    />

    <div v-loading="config.loading" class="config-editor-group__rows">
      <div v-for="item in items" :key="item.config_key" class="config-row">
        <div class="config-row__meta">
          <span class="config-row__key mono">{{ item.config_key }}</span>
          <span class="config-row__description">{{ item.description }}</span>
          <div class="config-row__tags">
            <el-tag size="small" effect="plain">{{ item.source }}</el-tag>
            <el-tag v-if="config.isDirty(item.config_key)" size="small" type="warning" effect="plain">
              {{ language.t("common.changed") }}
            </el-tag>
          </div>
        </div>

        <div class="config-row__control">
          <el-input-number
            v-if="isNumber(item)"
            :model-value="Number(config.draftValue(item.config_key))"
            :min="numberConstraint(item, 'min')"
            :max="numberConstraint(item, 'max')"
            :step="item.value_type === 'integer' ? 1 : 0.01"
            controls-position="right"
            :disabled="isFixed(item)"
            @update:model-value="updateNumber(item, $event)"
          />
          <el-switch
            v-else-if="item.value_type === 'boolean'"
            :model-value="Boolean(config.draftValue(item.config_key))"
            :disabled="isFixed(item)"
            @update:model-value="updateBoolean(item, $event)"
          />
          <el-input
            v-else
            :model-value="String(config.draftValue(item.config_key) ?? '')"
            :disabled="isFixed(item)"
            @update:model-value="updateString(item, $event)"
          />
          <el-button
            :icon="RefreshLeft"
            :title="language.t('common.reset')"
            :aria-label="language.t('common.reset')"
            :loading="config.saving"
            :disabled="config.loading"
            @click="$emit('reset-item', item.config_key)"
          />
        </div>
      </div>
      <el-empty v-if="!items.length" :description="language.t('config.noEditable')" />
    </div>

    <div class="config-editor-group__footer">
      <el-input
        v-model="reason"
        class="config-editor-group__reason"
        :placeholder="language.t('config.changeReason')"
        maxlength="120"
        clearable
      />
      <el-button
        type="primary"
        :icon="Check"
        :loading="config.saving"
        :disabled="!dirty || Boolean(validationMessage)"
        @click="$emit('save', reason)"
      >
        {{ language.t("common.save") }}
      </el-button>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { Check, RefreshLeft } from "@element-plus/icons-vue";
import { computed, ref } from "vue";

import { useConfigStore } from "@/stores/configStore";
import { useLanguageStore } from "@/stores/languageStore";
import type { ConfigValue, EditableConfigItem } from "@/types/config";

const props = defineProps<{
  title: string;
  category: string;
  items: EditableConfigItem[];
  danger?: boolean;
}>();

defineEmits<{
  save: [reason: string];
  "reset-item": [configKey: string];
}>();

const config = useConfigStore();
const language = useLanguageStore();
const reason = ref("");

const dirty = computed(() => config.categoryHasDirtyDrafts(props.category));
const validationMessage = computed(() => config.validateCategory(props.category));

function isNumber(item: EditableConfigItem): boolean {
  return item.value_type === "integer" || item.value_type === "number";
}

function isFixed(item: EditableConfigItem): boolean {
  return Object.prototype.hasOwnProperty.call(item.constraints, "fixed");
}

function numberConstraint(item: EditableConfigItem, key: "min" | "max"): number | undefined {
  const value = item.constraints[key];
  return typeof value === "number" ? value : undefined;
}

function updateNumber(item: EditableConfigItem, value: number | undefined) {
  updateValue(item, value);
}

function updateBoolean(item: EditableConfigItem, value: boolean) {
  updateValue(item, value);
}

function updateString(item: EditableConfigItem, value: string) {
  updateValue(item, value);
}

function updateValue(item: EditableConfigItem, value: string | number | boolean | undefined) {
  if (value === undefined) {
    return;
  }
  let nextValue: ConfigValue = value;
  if (item.value_type === "integer") {
    nextValue = Number.parseInt(String(value), 10);
  } else if (item.value_type === "number") {
    nextValue = Number(value);
  }
  config.updateDraft(item.config_key, nextValue);
}
</script>

<style scoped>
.config-editor-group {
  height: 100%;
}

.config-editor-group__header,
.config-editor-group__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.config-editor-group__alert {
  margin-bottom: 10px;
}

.config-editor-group__rows {
  display: grid;
  gap: 10px;
}

.config-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(220px, 300px);
  gap: 12px;
  align-items: center;
  padding: 10px 0;
  border-bottom: 1px solid #eef0f4;
}

.config-row:last-child {
  border-bottom: 0;
}

.config-row__meta,
.config-row__tags {
  display: flex;
  min-width: 0;
}

.config-row__meta {
  flex-direction: column;
  gap: 4px;
}

.config-row__key {
  overflow-wrap: anywhere;
  color: #344054;
  font-size: 12px;
  font-weight: 700;
}

.config-row__description {
  color: #667085;
  font-size: 12px;
}

.config-row__tags {
  flex-wrap: wrap;
  gap: 6px;
}

.config-row__control {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 40px;
  gap: 8px;
  align-items: center;
}

.config-editor-group__footer {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #eef0f4;
}

.config-editor-group__reason {
  min-width: 0;
}

@media (max-width: 760px) {
  .config-row {
    grid-template-columns: 1fr;
  }
}
</style>
