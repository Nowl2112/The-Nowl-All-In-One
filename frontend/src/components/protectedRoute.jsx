import React from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/authContext.jsx";

function ProtectedRoute({ children }) {
    const { currentUser, authLoading } = useAuth();
    const location = useLocation();

    if (authLoading) {
        return (
            <div
                style={{
                    minHeight: "100vh",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    background: "#f5f7fb",
                    color: "#6b7280",
                }}
            >
                Loading...
            </div>
        );
    }

    if (!currentUser) {
        return (
            <Navigate
                to="/login"
                replace
                state={{ from: location }}
            />
        );
    }

    return children;
}

export default ProtectedRoute;