import { Router } from "express";
import {
  createExpense,
  getExpenses,
  getExpenseById,
  updateExpense,
  deleteExpense,
} from "../controllers/expense.controller.js";
import { requireAuth } from "../middlewares/auth.middleware.js";
import { validate } from "../middlewares/validate.middleware.js";
import { validateObjectId } from "../middlewares/objectId.middleware.js";
import {
  createExpenseSchema,
  updateExpenseSchema,
  listExpensesQuerySchema,
} from "../validations/expense.schema.js";

const router = Router();

router.use(requireAuth);

router.post("/", validate(createExpenseSchema), createExpense);
router.get("/", validate(listExpensesQuerySchema, "query"), getExpenses);
router.get("/:id", validateObjectId, getExpenseById);
router.patch("/:id", validateObjectId, validate(updateExpenseSchema), updateExpense);
router.delete("/:id", validateObjectId, deleteExpense);

export default router;
