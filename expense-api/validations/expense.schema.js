import { z } from "zod";
import { CATEGORIES } from "../models/expense.model.js";

// note: there is no "user" field - the owner always comes from the JWT
export const createExpenseSchema = z.object({
  title: z.string().trim().min(2, "title must be at least 2 characters"),
  amount: z.number("amount must be a number").positive("amount must be greater than 0"),
  category: z.enum(CATEGORIES, `category must be one of: ${CATEGORIES.join(", ")}`),
  date: z.coerce.date("date must be a valid date").optional(),
  description: z.string().trim().optional(),
});

export const updateExpenseSchema = createExpenseSchema
  .partial()
  .refine((data) => Object.keys(data).length > 0, "at least one field is required");

export const listExpensesQuerySchema = z.object({
  category: z.enum(CATEGORIES).optional(),
  from: z.coerce.date().optional(),
  to: z.coerce.date().optional(),
  sortBy: z.enum(["date", "amount"]).default("date"),
  order: z.enum(["asc", "desc"]).default("desc"),
});
