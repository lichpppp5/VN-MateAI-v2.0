// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
'use client';

import * as React from 'react';
import * as SelectPrimitive from '@radix-ui/react-select';
import { Check, ChevronDown, ChevronUp } from 'lucide-react';
import { cn } from '@/lib/utils';

const Select = SelectPrimitive.Root;
const SelectGroup = SelectPrimitive.Group;
const SelectValue = SelectPrimitive.Value;

type SelectTriggerProps = React.ComponentPropsWithoutRef<typeof SelectPrimitive.Trigger> & { error?: boolean };
type SelectTriggerRef = React.ElementRef<typeof SelectPrimitive.Trigger>;

const SelectTrigger = React.forwardRef<SelectTriggerRef, SelectTriggerProps>(
  ({ className, children, error, ...props }, ref) => (
    <SelectPrimitive.Trigger
      ref={ref}
      className={cn(
        'select-cyber flex h-10 w-full items-center justify-between rounded-lg bg-vnmate-slate-900/50 border border-vnmate-slate-700 px-3 py-2 text-sm text-vnmate-neon placeholder:text-vnmate-slate-500 focus:border-vnmate-cyan focus:ring-2 focus:ring-vnmate-cyan/20 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50 [&>span]:line-clamp-1',
        error && 'border-vnmate-pink focus:border-vnmate-pink focus:ring-vnmate-pink/20',
        className
      )}
      {...props}
    >
      {children}
      <SelectPrimitive.Icon asChild>
        <ChevronDown className="h-4 w-4 opacity-50" />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  )
);
SelectTrigger.displayName = SelectPrimitive.Trigger.displayName;

type SelectScrollUpButtonProps = React.ComponentPropsWithoutRef<typeof SelectPrimitive.ScrollUpButton>;
type SelectScrollUpButtonRef = React.ElementRef<typeof SelectPrimitive.ScrollUpButton>;

const SelectScrollUpButton = React.forwardRef<SelectScrollUpButtonRef, SelectScrollUpButtonProps>(
  ({ className, ...props }, ref) => (
    <SelectPrimitive.ScrollUpButton
      ref={ref}
      className={cn('flex cursor-default items-center justify-center py-1', className)}
      {...props}
    >
      <ChevronUp className="h-4 w-4" />
    </SelectPrimitive.ScrollUpButton>
  )
);
SelectScrollUpButton.displayName = SelectPrimitive.ScrollUpButton.displayName;

type SelectScrollDownButtonProps = React.ComponentPropsWithoutRef<typeof SelectPrimitive.ScrollDownButton>;
type SelectScrollDownButtonRef = React.ElementRef<typeof SelectPrimitive.ScrollDownButton>;

const SelectScrollDownButton = React.forwardRef<SelectScrollDownButtonRef, SelectScrollDownButtonProps>(
  ({ className, ...props }, ref) => (
    <SelectPrimitive.ScrollDownButton
      ref={ref}
      className={cn('flex cursor-default items-center justify-center py-1', className)}
      {...props}
    >
      <ChevronDown className="h-4 w-4" />
    </SelectPrimitive.ScrollDownButton>
  )
);
SelectScrollDownButton.displayName = SelectPrimitive.ScrollDownButton.displayName;

type SelectContentProps = React.ComponentPropsWithoutRef<typeof SelectPrimitive.Content>;
type SelectContentRef = React.ElementRef<typeof SelectPrimitive.Content>;

const SelectContent = React.forwardRef<SelectContentRef, SelectContentProps>(
  ({ className, children, position = 'popper', ...props }, ref) => (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content
        ref={ref}
        className={cn(
          'relative z-50 max-h-96 min-w-[8rem] overflow-hidden rounded-lg border border-vnmate-slate-700 bg-vnmate-slate-900 text-vnmate-neon shadow-lg animate-in',
          position === 'popper' &&
            'data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2',
          className
        )}
        position={position}
        {...props}
      >
        <SelectScrollUpButton />
        <SelectPrimitive.Viewport
          className={cn(
            'p-1',
            position === 'popper' &&
              'h-[var(--radix-select-trigger-height)] w-full min-w-[var(--radix-select-trigger-width)]'
          )}
        >
          {children}
        </SelectPrimitive.Viewport>
        <SelectScrollDownButton />
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  )
);
SelectContent.displayName = SelectPrimitive.Content.displayName;

type SelectLabelProps = React.ComponentPropsWithoutRef<typeof SelectPrimitive.Label>;
type SelectLabelRef = React.ElementRef<typeof SelectPrimitive.Label>;

const SelectLabel = React.forwardRef<SelectLabelRef, SelectLabelProps>(
  ({ className, ...props }, ref) => (
    <SelectPrimitive.Label
      ref={ref}
      className={cn('py-1.5 pl-8 text-sm font-semibold text-vnmate-slate-400', className)}
      {...props}
    />
  )
);
SelectLabel.displayName = SelectPrimitive.Label.displayName;

type SelectItemProps = React.ComponentPropsWithoutRef<typeof SelectPrimitive.Item>;
type SelectItemRef = React.ElementRef<typeof SelectPrimitive.Item>;

const SelectItem = React.forwardRef<SelectItemRef, SelectItemProps>(
  ({ className, children, ...props }, ref) => (
    <SelectPrimitive.Item
      ref={ref}
      className={cn(
        'relative flex w-full cursor-default select-none items-center rounded-sm py-1.5 pl-8 pr-2 text-sm outline-none focus:bg-vnmate-cyan/10 focus:text-vnmate-cyan data-[disabled]:pointer-events-none data-[disabled]:opacity-50',
        className
      )}
      {...props}
    >
      <span className="absolute left-2 flex h-3.5 w-3.5 items-center justify-center">
        <SelectPrimitive.ItemIndicator>
          <Check className="h-4 w-4 text-vnmate-cyan" />
        </SelectPrimitive.ItemIndicator>
      </span>
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
    </SelectPrimitive.Item>
  )
);
SelectItem.displayName = SelectPrimitive.Item.displayName;

type SelectSeparatorProps = React.ComponentPropsWithoutRef<typeof SelectPrimitive.Separator>;
type SelectSeparatorRef = React.ElementRef<typeof SelectPrimitive.Separator>;

const SelectSeparator = React.forwardRef<SelectSeparatorRef, SelectSeparatorProps>(
  ({ className, ...props }, ref) => (
    <SelectPrimitive.Separator
      ref={ref}
      className={cn('-mx-1 my-1 h-px bg-vnmate-slate-700', className)}
      {...props}
    />
  )
);
SelectSeparator.displayName = SelectPrimitive.Separator.displayName;

export {
  Select,
  SelectGroup,
  SelectValue,
  SelectTrigger,
  SelectContent,
  SelectLabel,
  SelectItem,
  SelectSeparator,
  SelectScrollUpButton,
  SelectScrollDownButton,
};