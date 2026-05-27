import csv
import tkinter as tk
from tkinter import messagebox

def generate_csv(file_name, x_distance, y_distance, x_n, y_n):
    with open(file_name, mode='w', newline='') as file:
        writer = csv.writer(file)
        
        # Write header
        writer.writerow(["Subsite Name", "X Position", "Y Position", "Note"])
        
        subsite_id = 1
        for y in range(y_n):
            for x in range(x_n):
                x_pos = x * x_distance
                y_pos = y * y_distance
                writer.writerow([subsite_id, x_pos, y_pos, ""])
                subsite_id += 1
    
    messagebox.showinfo("Success", f"CSV file '{file_name}' has been generated successfully.")

def on_generate():
    try:
        x_distance = int(entry_x_distance.get())
        y_distance = int(entry_y_distance.get())
        x_n = int(entry_x_n.get())
        y_n = int(entry_y_n.get())
        generate_csv("grid_coordinates.csv", x_distance, y_distance, x_n, y_n)
    except ValueError:
        messagebox.showerror("Error", "Please enter valid integer values.")

# GUI Setup
root = tk.Tk()
root.title("Grid CSV Generator")

# Labels and Entry Fields
tk.Label(root, text="X Distance:").grid(row=0, column=0)
entry_x_distance = tk.Entry(root)
entry_x_distance.grid(row=0, column=1)

tk.Label(root, text="Y Distance:").grid(row=1, column=0)
entry_y_distance = tk.Entry(root)
entry_y_distance.grid(row=1, column=1)

tk.Label(root, text="X Points (x_n):").grid(row=2, column=0)
entry_x_n = tk.Entry(root)
entry_x_n.grid(row=2, column=1)

tk.Label(root, text="Y Points (y_n):").grid(row=3, column=0)
entry_y_n = tk.Entry(root)
entry_y_n.grid(row=3, column=1)

# Generate Button
tk.Button(root, text="Generate CSV", command=on_generate).grid(row=4, columnspan=2)

root.mainloop()
