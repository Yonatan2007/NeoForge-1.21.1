import Expense from "../models/expense.model.js";

const NOT_FOUND = { message: "Expense not found" };

export async function createExpense(req, res) {
  const expense = await Expense.create({ ...req.validated, user: req.userId });
  res.status(201).json({ expense });
}

export async function getExpenses(req, res) {
  const { category, from, to, sortBy, order } = req.validated;

  const filter = { user: req.userId };
  if (category) filter.category = category;
  if (from || to) {
    filter.date = {};
    if (from) filter.date.$gte = from;
    if (to) filter.date.$lte = to;
  }

  const expenses = await Expense.find(filter).sort({ [sortBy]: order === "asc" ? 1 : -1 });
  res.status(200).json({ count: expenses.length, expenses });
}

// every query below filters by both _id and user, so another user's expense is a plain 404
export async function getExpenseById(req, res) {
  const expense = await Expense.findOne({ _id: req.params.id, user: req.userId });
  if (!expense) return res.status(404).json(NOT_FOUND);
  res.status(200).json({ expense });
}

export async function updateExpense(req, res) {
  const expense = await Expense.findOneAndUpdate(
    { _id: req.params.id, user: req.userId },
    req.validated,
    { returnDocument: "after", runValidators: true }
  );
  if (!expense) return res.status(404).json(NOT_FOUND);
  res.status(200).json({ expense });
}

export async function deleteExpense(req, res) {
  const expense = await Expense.findOneAndDelete({ _id: req.params.id, user: req.userId });
  if (!expense) return res.status(404).json(NOT_FOUND);
  res.status(204).end();
}
