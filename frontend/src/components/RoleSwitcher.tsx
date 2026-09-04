import { useEffect, useState } from "react";
import { api } from "../api";
import type { Role } from "../api";
import { useAppState } from "../state/AppStateContext";
import "./RoleSwitcher.css";

export function RoleSwitcher() {
  const { persona, setPersona } = useAppState();
  const [roles, setRoles] = useState<Role[]>([]);

  useEffect(() => {
    api.getRoles().then(setRoles);
  }, []);

  return (
    <div className="role-switcher" role="tablist" aria-label="Switch persona">
      {roles.map((role) => (
        <button
          key={role.id}
          role="tab"
          aria-selected={role.id === persona}
          className={`role-switcher-btn ${role.id === persona ? "role-switcher-btn-active" : ""}`}
          onClick={() => setPersona(role.id)}
          title={role.description}
        >
          {role.name}
        </button>
      ))}
    </div>
  );
}
