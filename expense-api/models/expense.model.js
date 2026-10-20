import mongoose from "mongoose";

export const CATEGORIES = ["food", "transport", "shopping", "bills", "entertainment", "other"];

const expenseSchema = new mongoose.Schema(
  {
    title: { type: String, required: true, trim: true },
    amount: { type: Number, required: true, min: [0.01, "amount must be greater than 0"] },
    category: { type: String, required: true, enum: CATEGORIES },
    date: { type: Date, default: Date.now },
    description: { type: String, trim: true },
    user: { type: mongoose.Schema.Types.ObjectId, ref: "User", required: true, index: true },
  },
  {
    timestamps: true,
    toJSON: {
      transform(doc, ret) {
        delete ret.__v;
        return ret;
      },
    },
  }
);

export default mongoose.model("Expense", expenseSchema);
