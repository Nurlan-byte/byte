import { Icon } from "./Icon";

export function AiNotice() {
  return (
    <p className="ai-notice" role="note">
      <Icon name="info" />
      <span>
        BakePlan использует ИИ для прогноза продаж. Прогноз может ошибаться,
        количество для заказа или выпуска утверждаете вы.
      </span>
    </p>
  );
}
