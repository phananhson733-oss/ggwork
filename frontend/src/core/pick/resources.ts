import { z } from "zod";

export const resourceSchema = z.object({
  url: z
    .string()
    .url()
    .nullable()
    .refine(
      (value) =>
        value === null ||
        (value.startsWith("https://") &&
          !new URL(value).username &&
          !new URL(value).password),
    ),
  code: z.string().nullable(),
  label: z.string(),
});
