import { z } from "zod";

export const registerSchema = z.object({
  name: z.string().trim().min(2, "name must be at least 2 characters"),
  email: z.string().trim().toLowerCase().pipe(z.email("email must be valid")),
  password: z
    .string()
    .min(8, "password must be at least 8 characters")
    .regex(/[0-9]/, "password must contain a number")
    .regex(/[^A-Za-z0-9]/, "password must contain a special character"),
});

export const loginSchema = z.object({
  email: z.string().trim().toLowerCase().pipe(z.email("email must be valid")),
  password: z.string().min(1, "password is required"),
});
