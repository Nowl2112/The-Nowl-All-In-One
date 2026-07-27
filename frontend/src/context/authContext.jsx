import React, {
    createContext,
    useContext,
    useEffect,
    useMemo,
    useState,
} from "react";

import {
    createUserWithEmailAndPassword,
    onAuthStateChanged,
    signInWithEmailAndPassword,
    signOut,
} from "firebase/auth";

import { auth } from "../firebase.js";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
    const [currentUser, setCurrentUser] = useState(null);
    const [authLoading, setAuthLoading] = useState(true);

    useEffect(() => {
        const unsubscribe = onAuthStateChanged(
            auth,
            (user) => {
                setCurrentUser(user);
                setAuthLoading(false);
            },
            (error) => {
                console.error("Firebase authentication error:", error);
                setCurrentUser(null);
                setAuthLoading(false);
            }
        );

        return unsubscribe;
    }, []);

    async function login(email, password) {
        const normalizedEmail = email.trim().toLowerCase();

        const credential = await signInWithEmailAndPassword(
            auth,
            normalizedEmail,
            password
        );

        return credential.user;
    }

    async function register(email, password) {
        const normalizedEmail = email.trim().toLowerCase();

        const credential = await createUserWithEmailAndPassword(
            auth,
            normalizedEmail,
            password
        );

        return credential.user;
    }

    async function logout() {
        await signOut(auth);
    }

    async function getIdToken(forceRefresh = false) {
        if (!auth.currentUser) {
            return null;
        }

        return auth.currentUser.getIdToken(forceRefresh);
    }

    const value = useMemo(
        () => ({
            currentUser,
            authLoading,
            login,
            register,
            logout,
            getIdToken,
        }),
        [currentUser, authLoading]
    );

    return (
        <AuthContext.Provider value={value}>
            {children}
        </AuthContext.Provider>
    );
}

export function useAuth() {
    const context = useContext(AuthContext);

    if (!context) {
        throw new Error("useAuth must be used inside an AuthProvider");
    }

    return context;
}