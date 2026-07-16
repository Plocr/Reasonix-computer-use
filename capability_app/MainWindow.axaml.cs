using System.Collections.ObjectModel;
using System.Text.Json;
using Avalonia;
using Avalonia.Automation;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Interactivity;
using Avalonia.Threading;

namespace Reasonix.CapabilityApp;

public partial class MainWindow : Window
{
    private bool _dragging;
    private Point _dragOffset;
    public ObservableCollection<GridRow> Rows { get; } = new();

    public MainWindow()
    {
        InitializeComponent();
        for (var index = 1; index <= 100; index++)
            Rows.Add(new GridRow { Row = index, Value = index, Label = $"SYNTHETIC_ROW_{index}" });
        CapabilityGrid.ItemsSource = Rows;
    }

    private void SetResult(string eventName, object? value = null) =>
        ResultPanel.Text = JsonSerializer.Serialize(new { @event = eventName, value });

    private void ApplyInput(object? sender, RoutedEventArgs e) => SetResult("input_applied", InputText.Text ?? "");
    private void CheckChanged(object? sender, RoutedEventArgs e) => SetResult("check_changed", ToggleCheck.IsChecked);
    private void RadioChanged(object? sender, RoutedEventArgs e) =>
        SetResult("radio_changed", (sender as RadioButton)?.Content?.ToString());
    private void ChoiceChanged(object? sender, SelectionChangedEventArgs e)
    {
        // SelectionChanged fires while XAML fields are still being initialized.
        if (ChoiceCombo is not null && ResultPanel is not null)
            SetResult("choice_changed", (ChoiceCombo.SelectedItem as ComboBoxItem)?.Content?.ToString());
    }
    private void DuplicateOne(object? sender, RoutedEventArgs e) => SetResult("duplicate", 1);
    private void DuplicateTwo(object? sender, RoutedEventArgs e) => SetResult("duplicate", 2);
    private void MenuAction(object? sender, RoutedEventArgs e) => SetResult("menu_action", true);
    private void ReadGridSelection(object? sender, RoutedEventArgs e) =>
        SetResult("grid_selection", CapabilityGrid.SelectedItems.Count);

    private async void OpenModal(object? sender, RoutedEventArgs e)
    {
        var close = new Button { Content = "Close modal", Name = "CloseModal" };
        AutomationProperties.SetAutomationId(close, "CloseModal");
        var dialog = new Window { Title = "Synthetic Modal", Width = 360, Height = 180, Content = close };
        AutomationProperties.SetAutomationId(dialog, "SyntheticModal");
        close.Click += (_, _) => dialog.Close(true);
        await dialog.ShowDialog<bool>(this);
        SetResult("modal_closed", true);
    }

    private void OpenChild(object? sender, RoutedEventArgs e)
    {
        var child = new Window { Title = "Synthetic Child", Width = 420, Height = 240,
            Content = new TextBox { Text = "SYNTHETIC_CHILD", IsReadOnly = true } };
        AutomationProperties.SetAutomationId(child, "SyntheticChild");
        child.Show(this);
        SetResult("child_opened", true);
    }

    private void ReplaceWindow(object? sender, RoutedEventArgs e)
    {
        var replacement = new MainWindow { Title = "Reasonix Capability Lab - Replacement" };
        replacement.Show();
        Close();
    }

    private void MinimizeWindow(object? sender, RoutedEventArgs e) => WindowState = WindowState.Minimized;

    private void DragPressed(object? sender, PointerPressedEventArgs e)
    {
        _dragging = true;
        _dragOffset = e.GetPosition(DragTarget);
        e.Pointer.Capture(DragTarget);
    }

    private void DragMoved(object? sender, PointerEventArgs e)
    {
        if (!_dragging) return;
        var point = e.GetPosition(TestCanvas);
        Canvas.SetLeft(DragTarget, point.X - _dragOffset.X);
        Canvas.SetTop(DragTarget, point.Y - _dragOffset.Y);
        SetResult("dragging", new { x = point.X, y = point.Y });
    }

    private void DragReleased(object? sender, PointerReleasedEventArgs e)
    {
        _dragging = false;
        e.Pointer.Capture(null);
        SetResult("drag_complete", new { x = Canvas.GetLeft(DragTarget), y = Canvas.GetTop(DragTarget) });
    }

    private void CanvasWheel(object? sender, PointerWheelEventArgs e) => SetResult("canvas_wheel", e.Delta.Y);
    private void CanvasIcon(object? sender, RoutedEventArgs e) => SetResult("canvas_icon", true);

    private async void DelayedResult(object? sender, RoutedEventArgs e)
    {
        LoadingBar.IsIndeterminate = true;
        SetResult("loading", true);
        await Task.Delay(1200);
        LoadingBar.IsIndeterminate = false;
        LoadingBar.Value = 100;
        SetResult("delayed_complete", true);
    }

    private void NoChange(object? sender, RoutedEventArgs e) { }

    private async void ErrorDialog(object? sender, RoutedEventArgs e)
    {
        var close = new Button { Content = "Dismiss", Name = "DismissError" };
        AutomationProperties.SetAutomationId(close, "DismissError");
        var dialog = new Window { Title = "Synthetic Error", Width = 380, Height = 180,
            Content = new StackPanel { Margin = new Thickness(20), Spacing = 12,
                Children = { new TextBlock { Text = "SYNTHETIC_ERROR" }, close } } };
        close.Click += (_, _) => dialog.Close(true);
        await dialog.ShowDialog<bool>(this);
        SetResult("error_dismissed", true);
    }

    private void ReportFocus(object? sender, RoutedEventArgs e)
    {
        var focused = TopLevel.GetTopLevel(this)?.FocusManager?.GetFocusedElement() as Control;
        SetResult("focus", focused?.Name ?? focused?.GetType().Name ?? "none");
    }
}

public sealed class GridRow
{
    public int Row { get; set; }
    public int Value { get; set; }
    public string Label { get; set; } = "";
}
