import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { api } from "./api";
import {
  workspaceReference,
  type WorkspaceKey,
  type WorkspaceSession,
} from "./workspace";

const Session = createContext<WorkspaceSession>({
  bakery_id: null,
  operator_id: null,
});

export function useWorkspaceSession() {
  return useContext(Session);
}

export function useWorkspaceReference() {
  const session = useWorkspaceSession();
  return useCallback(
    (key: WorkspaceKey, value?: string | null) =>
      workspaceReference(key, value, session),
    [session],
  );
}

export function WorkspaceAccess({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<WorkspaceSession | null>(null);
  const [checking, setChecking] = useState(true);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let version = 0;
    async function verify() {
      const current = ++version;
      setChecking(true);
      try {
        const result = await api<WorkspaceSession>("/session");
        const demo = result?.bakery_id === null && result.operator_id === null;
        const assigned =
          typeof result?.bakery_id === "string" &&
          result.bakery_id.length > 0 &&
          typeof result.operator_id === "string" &&
          result.operator_id.length > 0;
        if (!demo && !assigned)
          throw new Error("Сервер вернул некорректный филиал.");
        if (current === version) {
          setSession((previous) =>
            previous?.operator_id === result.operator_id &&
            previous.bakery_id === result.bakery_id
              ? previous
              : result,
          );
        }
      } catch {
        if (current === version) setSession(null);
      } finally {
        if (current === version) setChecking(false);
      }
    }
    const visibilityChanged = () => {
      if (document.hidden) {
        ++version;
        setChecking(true);
      } else void verify();
    };
    const restorePage = (event: PageTransitionEvent) => {
      if (event.persisted) void verify();
    };
    void verify();
    document.addEventListener("visibilitychange", visibilityChanged);
    window.addEventListener("pageshow", restorePage);
    return () => {
      ++version;
      document.removeEventListener("visibilitychange", visibilityChanged);
      window.removeEventListener("pageshow", restorePage);
    };
  }, [attempt]);
  return (
    <>
      {(checking || !session) && (
        <main>
          {checking ? (
            <p role="status">Проверяем доступ к филиалу…</p>
          ) : (
            <>
              <p role="alert">Не удалось проверить доступ к филиалу.</p>
              <button onClick={() => setAttempt((previous) => previous + 1)}>
                Повторить
              </button>
            </>
          )}
        </main>
      )}
      {session && (
        <div hidden={checking}>
          <Session.Provider
            key={JSON.stringify([session.operator_id, session.bakery_id])}
            value={session}
          >
            {children}
          </Session.Provider>
        </div>
      )}
    </>
  );
}
