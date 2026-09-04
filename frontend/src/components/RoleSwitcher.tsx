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
          aria-describedby={`role-desc-${role.id}`}
          className={`role-switcher-btn ${role.id === persona ? "role-switcher-btn-active" : ""}`}
          onClick={() => setPersona(role.id)}
          title={role.description}
        >
          {role.name}
          {/* The description used to live only in the `title` hover tooltip
              above -- invisible to touch and most screen-reader flows. This
              keeps the tooltip for sighted mouse users but gives everyone
              else a real accessible description too. */}
          <span id={`role-desc-${role.id}`} className="sr-only">
            {role.description}
          </span>
        </button>
      ))}
    </div>
  );
}
