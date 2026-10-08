"use client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { UnauthorizedError } from "@/core/api/errors";
import { useAuth } from "@/core/auth/AuthProvider";

import {
  getEditingCapabilities,
  getEditingDevices,
  getEditingTask,
  listEditingTasks,
} from "./api";

export const editingKeys = {
  owner: (owner: string) => ["editing", owner] as const,
  task: (owner: string, id: string) => ["editing", owner, "task", id] as const,
};
const retry = (count: number, error: Error) =>
  !(error instanceof UnauthorizedError) && count < 1;
export function useEditingOwner() {
  const { user, applyUser } = useAuth();
  const client = useQueryClient();
  const owner = user?.id ?? "";
  const current = useRef(owner);
  current.current = owner;
  const previous = useRef(owner);
  useEffect(() => {
    const old = previous.current;
    previous.current = owner;
    if (old && old !== owner) {
      void client.cancelQueries({ queryKey: editingKeys.owner(old) });
      client.removeQueries({ queryKey: editingKeys.owner(old) });
    }
  }, [client, owner]);
  return {
    owner,
    current,
    expire: (error: unknown) => {
      if (error instanceof UnauthorizedError) applyUser(null);
    },
  };
}
export function useEditingTask(id: string) {
  const { owner, expire } = useEditingOwner();
  const query = useQuery({
    queryKey: editingKeys.task(owner, id),
    queryFn: ({ signal }) => getEditingTask(id, signal),
    enabled: !!owner && !!id,
    retry,
    refetchInterval: 5000,
  });
  useEffect(() => {
    expire(query.error);
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps
  return { ...query, data: owner ? query.data : undefined };
}
export function useEditingSetup() {
  const { owner, expire } = useEditingOwner();
  const devices = useQuery({
    queryKey: [...editingKeys.owner(owner), "devices"],
    queryFn: ({ signal }) => getEditingDevices(signal),
    enabled: !!owner,
    retry,
    refetchInterval: 5000,
  });
  const capabilities = useQuery({
    queryKey: [...editingKeys.owner(owner), "capabilities"],
    queryFn: ({ signal }) => getEditingCapabilities(signal),
    enabled: !!owner,
    retry,
    refetchInterval: 15000,
  });
  useEffect(() => {
    expire(devices.error ?? capabilities.error);
  }, [devices.error, capabilities.error]); // eslint-disable-line react-hooks/exhaustive-deps
  return { owner, devices, capabilities };
}
export function useEditingHistory(offset: number) {
  const { owner, expire } = useEditingOwner();
  const query = useQuery({
    queryKey: [...editingKeys.owner(owner), "history", offset],
    queryFn: ({ signal }) => listEditingTasks(offset, signal),
    enabled: !!owner,
    retry,
    refetchInterval: offset === 0 ? 5000 : false,
  });
  useEffect(() => {
    expire(query.error);
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps
  return { ...query, data: owner ? query.data : undefined };
}
