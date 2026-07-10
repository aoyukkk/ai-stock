ALTER TABLE model_validation_order_plan ADD COLUMN risk_reward_to_tp1 NUMERIC(10, 4);
ALTER TABLE model_validation_order_plan ADD COLUMN risk_reward_to_tp2 NUMERIC(10, 4);
ALTER TABLE model_validation_order_plan ADD COLUMN active_risk_reward NUMERIC(10, 4);
ALTER TABLE model_validation_order_plan ADD COLUMN active_target_mode VARCHAR(32);
ALTER TABLE model_validation_order_plan ADD COLUMN unrounded_stop_loss_price NUMERIC(14, 4);
